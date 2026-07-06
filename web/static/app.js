const state = {
  sessions: [],
  activeSessionId: "",
  messages: [],
  sending: false,
  search: "",
  ws: null,
  wsReady: null,
  activeTurn: null,
  model: {
    endpoint: "",
    currentModel: "",
    models: [],
  },
};

const elements = {
  shell: document.querySelector(".app-shell"),
  collapseButton: document.querySelector("#collapseButton"),
  newChatButton: document.querySelector("#newChatButton"),
  searchInput: document.querySelector("#searchInput"),
  recentList: document.querySelector("#recentList"),
  chatWrap: document.querySelector(".chat-wrap"),
  emptyState: document.querySelector("#emptyState"),
  messageList: document.querySelector("#messageList"),
  composer: document.querySelector("#composer"),
  messageInput: document.querySelector("#messageInput"),
  sendButton: document.querySelector("#sendButton"),
  stopButton: document.querySelector("#stopButton"),
  modelSelect: document.querySelector("#modelSelect"),
};

let lastModelDropdownRefreshAt = 0;

async function fetchSessions() {
  const response = await fetch("/api/sessions");
  return readJson(response);
}

async function fetchSession(sessionId) {
  const response = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}`);
  return readJson(response);
}

async function sendMessageStream(sessionId, message, model, handlers = {}) {
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, message, model }),
  });
  return readJson(response);
}

async function sendStreamingMessage(sessionId, message, model, handlers = {}) {
  const response = await fetch("/api/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, message, model }),
  });
  if (!response.ok) {
    return readJson(response);
  }
  if (!response.body) {
    const payload = await sendMessageStream(sessionId, message, model, handlers);
    handlers.onDelta?.(payload.assistant?.content || "");
    handlers.onDone?.(payload);
    return payload;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finalPayload = null;

  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    const parsed = consumeSseBuffer(buffer, handlers);
    buffer = parsed.remaining;
    finalPayload = parsed.finalPayload || finalPayload;
  }

  buffer += decoder.decode();
  const parsed = consumeSseBuffer(buffer, handlers, { flush: true });
  finalPayload = parsed.finalPayload || finalPayload;
  return finalPayload;
}

async function fetchModels() {
  const response = await fetch("/api/models");
  return readJson(response);
}

async function setModelPreference(model) {
  const response = await fetch("/api/model/preference", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model }),
  });
  return readJson(response);
}

async function renameSession(sessionId, title) {
  const response = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  return readJson(response);
}

async function deleteSession(sessionId) {
  const response = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}`, {
    method: "DELETE",
  });
  return readJson(response);
}

async function readJson(response) {
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = payload.error || {};
    throw new Error(error.message || "Request failed");
  }
  return payload;
}

function websocketUrl() {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/ws`;
}

function ensureWebSocket() {
  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    return Promise.resolve(state.ws);
  }
  if (state.wsReady) {
    return state.wsReady;
  }

  state.ws = new WebSocket(websocketUrl());
  state.wsReady = new Promise((resolve, reject) => {
    state.ws.addEventListener(
      "open",
      () => {
        state.ws.send(JSON.stringify({ type: "hello", client: "web" }));
        state.wsReady = null;
        resolve(state.ws);
      },
      { once: true }
    );
    state.ws.addEventListener(
      "error",
      () => {
        state.wsReady = null;
        reject(new Error("WebSocket connection failed"));
      },
      { once: true }
    );
  });
  state.ws.addEventListener("message", handleWebSocketMessage);
  state.ws.addEventListener("close", () => {
    if (state.activeTurn) {
      state.activeTurn.reject(new Error("WebSocket connection closed"));
      state.activeTurn = null;
    }
    state.ws = null;
    state.wsReady = null;
    state.sending = false;
    updateComposerState();
  });
  return state.wsReady;
}

async function sendWebSocketMessage(sessionId, message, model) {
  const socket = await ensureWebSocket();
  return new Promise((resolve, reject) => {
    state.activeTurn = { sessionId, resolve, reject };
    socket.send(JSON.stringify({
      type: "message",
      session_id: sessionId,
      content: message,
      model,
    }));
  });
}

function handleWebSocketMessage(event) {
  const payload = JSON.parse(event.data);
  if (payload.event === "channel_text") {
    const active = state.activeTurn;
    if (!active || payload.session_id !== active.sessionId) {
      return;
    }
    const pending = state.messages.find((message) => message.isPending);
    if (pending) {
      pending.content += String(payload.data || "");
      renderMessages();
    }
    return;
  }
  if (payload.event !== "channel_status") {
    return;
  }

  const status = payload.data || {};
  if (status.type === "accepted") {
    return;
  }
  const active = state.activeTurn;
  if (!active || payload.session_id !== active.sessionId) {
    return;
  }
  if (status.type === "done" || status.type === "stopped") {
    active.resolve(status);
    state.activeTurn = null;
    return;
  }
  if (status.type === "error") {
    const error = status.error || {};
    active.reject(new Error(error.message || "Request failed"));
    state.activeTurn = null;
  }
}

async function stopActiveTurn() {
  if (!state.sending || !state.activeSessionId) {
    return;
  }
  const socket = await ensureWebSocket();
  socket.send(JSON.stringify({ type: "stop", session_id: state.activeSessionId }));
}

function consumeSseBuffer(buffer, handlers, { flush = false } = {}) {
  let remaining = buffer.replace(/\r\n/g, "\n");
  let finalPayload = null;
  while (true) {
    const boundary = remaining.indexOf("\n\n");
    if (boundary === -1) {
      break;
    }
    const block = remaining.slice(0, boundary);
    remaining = remaining.slice(boundary + 2);
    finalPayload = handleSseEvent(block, handlers) || finalPayload;
  }
  if (flush && remaining.trim()) {
    finalPayload = handleSseEvent(remaining, handlers) || finalPayload;
    remaining = "";
  }
  return { remaining, finalPayload };
}

function handleSseEvent(block, handlers) {
  const event = parseSseEvent(block);
  if (!event) {
    return null;
  }
  if (event.type === "status") {
    handlers.onStatus?.(event.data);
    return null;
  }
  if (event.type === "delta") {
    handlers.onDelta?.(event.data.content || "");
    return null;
  }
  if (event.type === "done") {
    handlers.onDone?.(event.data);
    return event.data;
  }
  if (event.type === "stopped") {
    handlers.onDone?.({ ...event.data, type: "stopped" });
    return { ...event.data, type: "stopped" };
  }
  if (event.type === "error") {
    const message = event.data?.error?.message || "Request failed";
    throw new Error(message);
  }
  return null;
}

function parseSseEvent(block) {
  let type = "";
  const dataLines = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) {
      type = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trimStart());
    }
  }
  if (!type) {
    return null;
  }
  const rawData = dataLines.join("\n") || "{}";
  return { type, data: JSON.parse(rawData) };
}

function normalizeModelPayload(payload) {
  return {
    endpoint: payload.endpoint || "",
    currentModel: payload.current_model || "",
    models: payload.models || [],
  };
}

function renderModelSelect() {
  elements.modelSelect.replaceChildren();
  const models = state.model.models;
  if (!models.length) {
    const option = document.createElement("option");
    option.value = "auto";
    option.textContent = "auto";
    elements.modelSelect.append(option);
    elements.modelSelect.value = "auto";
    elements.modelSelect.disabled = true;
    return;
  }

  for (const model of models) {
    const option = document.createElement("option");
    option.value = model;
    option.textContent = model;
    elements.modelSelect.append(option);
  }
  elements.modelSelect.value = state.model.currentModel;
  elements.modelSelect.disabled = state.sending;
}

function renderSessions() {
  const query = state.search.trim().toLowerCase();
  const sessions = state.sessions.filter((session) => {
    if (!query) {
      return true;
    }
    return `${session.session_id} ${session.title || ""} ${session.preview}`.toLowerCase().includes(query);
  });

  elements.recentList.replaceChildren();
  if (!sessions.length) {
    const empty = document.createElement("div");
    empty.className = "empty-recent";
    empty.textContent = state.search ? "No matching chats" : "No recent chats";
    elements.recentList.append(empty);
    return;
  }

  for (const session of sessions) {
    const row = document.createElement("div");
    row.className = "recent-row";
    if (session.session_id === state.activeSessionId) {
      row.classList.add("is-active");
    }

    const item = document.createElement("button");
    item.type = "button";
    item.className = "recent-item";
    item.addEventListener("click", () => openSession(session.session_id));

    const title = document.createElement("span");
    title.className = "recent-title";
    title.textContent = session.title || session.preview || session.session_id;

    const meta = document.createElement("span");
    meta.className = "recent-meta";
    meta.textContent = `${session.message_count} messages`;

    item.append(title, meta);

    const actions = document.createElement("span");
    actions.className = "recent-actions";
    const renameButton = document.createElement("button");
    renameButton.type = "button";
    renameButton.className = "recent-action";
    renameButton.title = "Rename";
    renameButton.textContent = "R";
    renameButton.addEventListener("click", (event) => {
      event.stopPropagation();
      promptRenameSession(session);
    });
    const deleteButton = document.createElement("button");
    deleteButton.type = "button";
    deleteButton.className = "recent-action";
    deleteButton.title = "Delete";
    deleteButton.textContent = "D";
    deleteButton.disabled = state.sending && session.session_id === state.activeSessionId;
    deleteButton.addEventListener("click", (event) => {
      event.stopPropagation();
      confirmDeleteSession(session.session_id);
    });
    actions.append(renameButton, deleteButton);
    row.append(item, actions);
    elements.recentList.append(row);
  }
}

function renderMessages() {
  elements.messageList.replaceChildren();
  const visibleMessages = state.messages.filter((message) =>
    ["user", "assistant"].includes(message.role)
  );
  elements.emptyState.hidden = visibleMessages.length > 0;

  for (const message of visibleMessages) {
    elements.messageList.append(createMessageNode(message));
  }
  requestAnimationFrame(() => {
    elements.chatWrap.scrollTop = elements.chatWrap.scrollHeight;
  });
}

function createMessageNode(message) {
  const wrap = document.createElement("article");
  wrap.className = `message ${message.role}`;
  if (message.isError) {
    wrap.classList.add("error");
  }
  if (message.isPending) {
    wrap.classList.add("pending");
  }

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  if (message.role === "assistant" && message.isPending && !message.content) {
    bubble.classList.add("typing-bubble");
    bubble.append(createTypingIndicator());
  } else if (message.role === "assistant" && !message.isError) {
    bubble.classList.add("markdown");
    bubble.append(renderMarkdown(message.content || ""));
    if (message.isPending) {
      bubble.append(createStreamingCursor());
    }
  } else {
    bubble.textContent = message.content;
  }
  wrap.append(bubble);
  return wrap;
}

function createTypingIndicator() {
  const indicator = document.createElement("span");
  indicator.className = "typing-indicator";
  indicator.setAttribute("aria-label", "ZhiCe-Agent is thinking");
  for (let index = 0; index < 3; index += 1) {
    const dot = document.createElement("span");
    dot.setAttribute("aria-hidden", "true");
    indicator.append(dot);
  }
  return indicator;
}

function createStreamingCursor() {
  const cursor = document.createElement("span");
  cursor.className = "streaming-cursor";
  cursor.setAttribute("aria-hidden", "true");
  return cursor;
}

function renderMarkdown(text) {
  const fragment = document.createDocumentFragment();
  const lines = String(text || "").replace(/\r\n/g, "\n").split("\n");
  let paragraph = [];
  let list = null;
  let codeBlock = null;

  const flushParagraph = () => {
    if (!paragraph.length) {
      return;
    }
    const p = document.createElement("p");
    appendInlineMarkdown(p, paragraph.join(" "));
    fragment.append(p);
    paragraph = [];
  };

  const flushList = () => {
    if (list) {
      fragment.append(list);
      list = null;
    }
  };

  const flushCodeBlock = () => {
    if (!codeBlock) {
      return;
    }
    const pre = document.createElement("pre");
    const code = document.createElement("code");
    code.textContent = codeBlock.lines.join("\n");
    pre.append(code);
    fragment.append(pre);
    codeBlock = null;
  };

  for (const rawLine of lines) {
    const line = rawLine.trimEnd();
    const trimmed = line.trim();

    if (trimmed.startsWith("```")) {
      if (codeBlock) {
        flushCodeBlock();
      } else {
        flushParagraph();
        flushList();
        codeBlock = { lines: [] };
      }
      continue;
    }
    if (codeBlock) {
      codeBlock.lines.push(rawLine);
      continue;
    }
    if (!trimmed) {
      flushParagraph();
      flushList();
      continue;
    }

    const heading = trimmed.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      flushParagraph();
      flushList();
      const level = String(Math.min(heading[1].length + 1, 4));
      const node = document.createElement(`h${level}`);
      appendInlineMarkdown(node, heading[2]);
      fragment.append(node);
      continue;
    }

    const unordered = trimmed.match(/^[-*]\s+(.+)$/);
    if (unordered) {
      flushParagraph();
      if (!list || list.tagName !== "UL") {
        flushList();
        list = document.createElement("ul");
      }
      const item = document.createElement("li");
      appendInlineMarkdown(item, unordered[1]);
      list.append(item);
      continue;
    }

    const ordered = trimmed.match(/^\d+\.\s+(.+)$/);
    if (ordered) {
      flushParagraph();
      if (!list || list.tagName !== "OL") {
        flushList();
        list = document.createElement("ol");
      }
      const item = document.createElement("li");
      appendInlineMarkdown(item, ordered[1]);
      list.append(item);
      continue;
    }

    flushList();
    paragraph.push(trimmed);
  }

  flushCodeBlock();
  flushParagraph();
  flushList();
  if (!fragment.childNodes.length) {
    fragment.append(document.createTextNode(""));
  }
  return fragment;
}

function appendInlineMarkdown(parent, text) {
  const pattern = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\))/g;
  let position = 0;
  for (const match of text.matchAll(pattern)) {
    if (match.index > position) {
      parent.append(document.createTextNode(text.slice(position, match.index)));
    }
    const token = match[0];
    if (token.startsWith("**")) {
      const strong = document.createElement("strong");
      strong.textContent = token.slice(2, -2);
      parent.append(strong);
    } else if (token.startsWith("`")) {
      const code = document.createElement("code");
      code.textContent = token.slice(1, -1);
      parent.append(code);
    } else {
      parent.append(createSafeLink(token));
    }
    position = match.index + token.length;
  }
  if (position < text.length) {
    parent.append(document.createTextNode(text.slice(position)));
  }
}

function createSafeLink(token) {
  const match = token.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
  if (!match) {
    return document.createTextNode(token);
  }
  const href = match[2].trim();
  if (!/^https?:\/\//i.test(href) && !/^mailto:/i.test(href)) {
    return document.createTextNode(match[1]);
  }
  const link = document.createElement("a");
  link.textContent = match[1];
  link.href = href;
  link.rel = "noreferrer";
  link.target = "_blank";
  return link;
}

async function refreshSessions({ openFirst = false } = {}) {
  try {
    const payload = await fetchSessions();
    state.sessions = payload.sessions || [];
    if (openFirst && !state.activeSessionId && state.sessions.length) {
      await openSession(state.sessions[0].session_id);
      return;
    }
    renderSessions();
  } catch (error) {
    showTransientError(error.message);
  }
}

async function refreshModels({ showError = true } = {}) {
  try {
    const payload = await fetchModels();
    state.model = normalizeModelPayload(payload);
  } catch (error) {
    state.model = { endpoint: "", currentModel: "", models: [] };
    if (showError) {
      showTransientError(error.message);
    }
  }
  renderModelSelect();
}

async function refreshModelsForDropdown() {
  const now = Date.now();
  if (now - lastModelDropdownRefreshAt < 500) {
    return;
  }
  lastModelDropdownRefreshAt = now;
  await refreshModels({ showError: false });
}

async function openSession(sessionId) {
  state.activeSessionId = sessionId;
  try {
    const payload = await fetchSession(sessionId);
    state.messages = payload.messages || [];
  } catch (error) {
    state.messages = [{ role: "assistant", content: error.message, isError: true }];
  }
  renderSessions();
  renderMessages();
}

async function promptRenameSession(session) {
  const current = session.title || session.preview || session.session_id;
  const title = window.prompt("Rename chat", current);
  if (title === null) {
    return;
  }
  const normalized = title.trim();
  if (!normalized) {
    return;
  }
  try {
    await renameSession(session.session_id, normalized);
    await refreshSessions();
  } catch (error) {
    showTransientError(error.message);
  }
}

async function confirmDeleteSession(sessionId) {
  if (!window.confirm("Delete this chat?")) {
    return;
  }
  try {
    await deleteSession(sessionId);
    if (sessionId === state.activeSessionId) {
      state.activeSessionId = "";
      state.messages = [];
      state.activeTurn = null;
    }
    await refreshSessions();
    renderMessages();
  } catch (error) {
    showTransientError(error.message);
  }
}

function newSession() {
  state.activeSessionId = `session-${formatDateId(new Date())}`;
  state.messages = [];
  elements.messageInput.focus();
  renderSessions();
  renderMessages();
}

function isSlashCommand(text) {
  return text.trim().startsWith("/");
}

function isModelCommand(text) {
  return /^\/model(?:\s|$)/i.test(text.trim());
}

async function handleSubmit(event) {
  event.preventDefault();
  if (state.sending) {
    return;
  }

  const text = elements.messageInput.value.trim();
  if (!text) {
    return;
  }
  const shouldRefreshModels = isModelCommand(text);
  if (!state.activeSessionId) {
    state.activeSessionId = `session-${formatDateId(new Date())}`;
  }

  state.sending = true;
  updateComposerState();
  elements.messageInput.value = "";
  autoSizeTextarea();

  state.messages.push({ role: "user", content: text });
  const pendingMessage = { role: "assistant", content: "", isPending: true };
  state.messages.push(pendingMessage);
  renderMessages();

  try {
    const selectedModel =
      state.model.currentModel && !isSlashCommand(text) ? elements.modelSelect.value : "";
    const status = await sendWebSocketMessage(state.activeSessionId, text, selectedModel);
    const assistant = status.assistant || {};
    pendingMessage.role = assistant.role || "assistant";
    pendingMessage.content = assistant.content || pendingMessage.content;
    pendingMessage.name = assistant.name;
    pendingMessage.tool_call_id = assistant.tool_call_id;
    pendingMessage.tool_calls = assistant.tool_calls || [];
    pendingMessage.metadata = assistant.metadata || {};
    if (status.type === "stopped" && !pendingMessage.content) {
      pendingMessage.content = "Stopped.";
    }
    pendingMessage.isPending = false;
    if (shouldRefreshModels) {
      await refreshModels({ showError: false });
    }
    await refreshSessions();
  } catch (error) {
    pendingMessage.content = error.message;
    pendingMessage.isPending = false;
    pendingMessage.isError = true;
    if (shouldRefreshModels) {
      await refreshModels({ showError: false });
    }
  } finally {
    state.sending = false;
    updateComposerState();
    renderSessions();
    renderMessages();
    elements.messageInput.focus();
  }
}

async function handleModelChange() {
  const selectedModel = elements.modelSelect.value;
  if (!selectedModel || selectedModel === state.model.currentModel) {
    return;
  }
  elements.modelSelect.disabled = true;
  try {
    const payload = await setModelPreference(selectedModel);
    state.model = normalizeModelPayload(payload);
  } catch (error) {
    showTransientError(error.message);
  } finally {
    renderModelSelect();
    updateComposerState();
  }
}

function updateComposerState() {
  const hasText = elements.messageInput.value.trim().length > 0;
  elements.sendButton.disabled = state.sending || !hasText;
  elements.stopButton.disabled = !state.sending;
  elements.modelSelect.disabled = state.sending || state.model.models.length === 0;
  elements.sendButton.setAttribute("aria-busy", state.sending ? "true" : "false");
  elements.stopButton.setAttribute("aria-busy", state.sending ? "true" : "false");
}

function autoSizeTextarea() {
  elements.messageInput.style.height = "auto";
  elements.messageInput.style.height = `${Math.min(elements.messageInput.scrollHeight, 160)}px`;
}

function showTransientError(message) {
  state.messages = [{ role: "assistant", content: message, isError: true }];
  renderMessages();
}

function formatDateId(date) {
  const pad = (value, size = 2) => String(value).padStart(size, "0");
  return [
    date.getFullYear(),
    pad(date.getMonth() + 1),
    pad(date.getDate()),
    "-",
    pad(date.getHours()),
    pad(date.getMinutes()),
    pad(date.getSeconds()),
    "-",
    pad(date.getMilliseconds(), 3),
  ].join("");
}

elements.collapseButton.addEventListener("click", () => {
  elements.shell.classList.toggle("is-collapsed");
});

elements.newChatButton.addEventListener("click", newSession);

elements.searchInput.addEventListener("input", (event) => {
  state.search = event.target.value;
  renderSessions();
});

elements.messageInput.addEventListener("input", () => {
  autoSizeTextarea();
  updateComposerState();
});

elements.messageInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    elements.composer.requestSubmit();
  }
});

elements.modelSelect.addEventListener("change", handleModelChange);
elements.modelSelect.addEventListener("focus", () => {
  refreshModelsForDropdown();
});
elements.modelSelect.addEventListener("pointerdown", () => {
  refreshModelsForDropdown();
});

elements.stopButton.addEventListener("click", stopActiveTurn);

elements.composer.addEventListener("submit", handleSubmit);

ensureWebSocket().catch((error) => showTransientError(error.message));
refreshModels();
refreshSessions({ openFirst: true });
updateComposerState();
