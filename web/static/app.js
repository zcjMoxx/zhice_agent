const state = {
  sessions: [],
  activeSessionId: "",
  messages: [],
  sending: false,
  search: "",
  ws: null,
  wsReady: null,
  activeTurn: null,
  authorizationRefresh: null,
  currentUser: null,
  permissions: [],
  pendingConfirmation: null,
  adminTab: "users",
  model: {
    endpoint: "",
    currentModel: "",
    models: [],
  },
};

const elements = {
  appShell: document.querySelector("#appShell"),
  loginView: document.querySelector("#loginView"),
  loginForm: document.querySelector("#loginForm"),
  loginUsername: document.querySelector("#loginUsername"),
  loginPassword: document.querySelector("#loginPassword"),
  loginButton: document.querySelector("#loginButton"),
  showRegisterButton: document.querySelector("#showRegisterButton"),
  loginHint: document.querySelector("#loginHint"),
  loginError: document.querySelector("#loginError"),
  bootstrapDialog: document.querySelector("#bootstrapDialog"),
  bootstrapForm: document.querySelector("#bootstrapForm"),
  bootstrapClose: document.querySelector("#bootstrapClose"),
  bootstrapCancel: document.querySelector("#bootstrapCancel"),
  bootstrapSetupToken: document.querySelector("#bootstrapSetupToken"),
  bootstrapPassword: document.querySelector("#bootstrapPassword"),
  bootstrapError: document.querySelector("#bootstrapError"),
  bootstrapSubmit: document.querySelector("#bootstrapSubmit"),
  registerDialog: document.querySelector("#registerDialog"),
  registerForm: document.querySelector("#registerForm"),
  registerClose: document.querySelector("#registerClose"),
  registerCancel: document.querySelector("#registerCancel"),
  registerUsername: document.querySelector("#registerUsername"),
  registerPassword: document.querySelector("#registerPassword"),
  registerPasswordConfirm: document.querySelector("#registerPasswordConfirm"),
  registerError: document.querySelector("#registerError"),
  registerSubmit: document.querySelector("#registerSubmit"),
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
  userEntry: document.querySelector("#userEntry"),
  userAvatarPrimary: document.querySelector("#userAvatarPrimary"),
  userAvatarSecondary: document.querySelector("#userAvatarSecondary"),
  userName: document.querySelector("#userName"),
  userMeta: document.querySelector("#userMeta"),
  userMenu: document.querySelector("#userMenu"),
  accountSettingsButton: document.querySelector("#accountSettingsButton"),
  adminButton: document.querySelector("#adminButton"),
  logoutButton: document.querySelector("#logoutButton"),
  accountDialog: document.querySelector("#accountDialog"),
  accountClose: document.querySelector("#accountClose"),
  profileForm: document.querySelector("#profileForm"),
  accountUsername: document.querySelector("#accountUsername"),
  accountDisplayName: document.querySelector("#accountDisplayName"),
  profileStatus: document.querySelector("#profileStatus"),
  passwordForm: document.querySelector("#passwordForm"),
  currentPassword: document.querySelector("#currentPassword"),
  newPassword: document.querySelector("#newPassword"),
  confirmPassword: document.querySelector("#confirmPassword"),
  passwordStatus: document.querySelector("#passwordStatus"),
  adminPage: document.querySelector("#adminPage"),
  adminLogoutButton: document.querySelector("#adminLogoutButton"),
  managementClose: document.querySelector("#managementClose"),
  managementBody: document.querySelector("#managementBody"),
  confirmationDialog: document.querySelector("#confirmationDialog"),
  confirmationTitle: document.querySelector("#confirmationTitle"),
  confirmationDetails: document.querySelector("#confirmationDetails"),
  confirmationApprove: document.querySelector("#confirmationApprove"),
  confirmationDeny: document.querySelector("#confirmationDeny"),
};

let lastModelDropdownRefreshAt = 0;
const isOwnerSetupRoute = window.location.pathname.replace(/\/+$/, "") === "/_setup";
const isAdminRoute = window.location.pathname.replace(/\/+$/, "") === "/admin";

async function fetchSessions() {
  const response = await fetch("/api/sessions");
  return readJson(response);
}

async function fetchCurrentUser() {
  const response = await fetch("/api/auth/me");
  return readJson(response);
}

async function login(username, password) {
  const response = await fetch("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  return readJson(response);
}

async function bootstrapOwner(setupToken, password) {
  const response = await fetch("/api/auth/bootstrap", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      setup_token: setupToken,
      password,
    }),
  });
  return readJson(response);
}

async function registerUser(username, password) {
  const response = await fetch("/api/auth/register", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      username,
      password,
    }),
  });
  return readJson(response);
}

async function logout() {
  const response = await fetch("/api/auth/logout", { method: "POST" });
  return readJson(response);
}

async function updateCurrentProfile(displayName) {
  const response = await fetch("/api/auth/profile", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ display_name: displayName }),
  });
  return readJson(response);
}

async function changeCurrentPassword(currentPassword, newPassword) {
  const response = await fetch("/api/auth/password", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      current_password: currentPassword,
      new_password: newPassword,
    }),
  });
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

async function fetchModels(sessionId) {
  const response = await fetch(`/api/models?session_id=${encodeURIComponent(sessionId)}`);
  return readJson(response);
}

async function setModelPreference(sessionId, model) {
  const response = await fetch("/api/model/preference", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, model }),
  });
  return readJson(response);
}

async function fetchAdminUsers() {
  return readJson(await fetch("/api/admin/users"));
}

async function createAdminUser(payload) {
  return readJson(await fetch("/api/admin/users", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }));
}

async function updateAdminUser(userId, payload) {
  return readJson(await fetch(`/api/admin/users/${encodeURIComponent(userId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }));
}

async function fetchAdminRoles() {
  return readJson(await fetch("/api/admin/roles"));
}

async function updateAdminRole(roleId, permissionKeys) {
  return readJson(await fetch(`/api/admin/roles/${encodeURIComponent(roleId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ permission_keys: permissionKeys }),
  }));
}

async function fetchAuditEvents() {
  return readJson(await fetch("/api/audit/events?limit=100"));
}

async function decideConfirmation(confirmationId, approve) {
  const action = approve ? "approve" : "deny";
  return readJson(await fetch(
    `/api/tool-confirmations/${encodeURIComponent(confirmationId)}/${action}`,
    { method: "POST" }
  ));
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
    const code = error.code || "REQUEST_FAILED";
    const status = Number(error.status || response.status);
    const failure = new Error(`HTTP ${status} (${code}): ${error.message || "Request failed"}`);
    failure.code = code;
    failure.status = status;
    failure.requestId = error.request_id || response.headers.get("X-Request-ID") || "";
    failure.details = error.details || {};
    if (status === 401 || status === 403) {
      await refreshAuthorizationAfterFailure(status);
    }
    throw failure;
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
  if (payload.event === "tool_confirmation_required") {
    showToolConfirmation(payload.data || {});
    return;
  }
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
    if (error.code === "AUTH_REQUIRED") {
      showLogin("Your login expired. Sign in again.");
    }
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
  if (!state.activeSessionId) {
    state.activeSessionId = `session-${formatDateId(new Date())}`;
  }
  try {
    const payload = await fetchModels(state.activeSessionId);
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
  await refreshModels({ showError: false });
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

async function newSession() {
  state.activeSessionId = `session-${formatDateId(new Date())}`;
  state.messages = [];
  elements.messageInput.focus();
  renderSessions();
  renderMessages();
  await refreshModels({ showError: false });
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
      !isSlashCommand(text) && elements.modelSelect.value !== state.model.currentModel
        ? elements.modelSelect.value
        : "";
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
    const payload = await setModelPreference(state.activeSessionId, selectedModel);
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

function hasPermission(permission) {
  return state.permissions.includes(permission);
}

function getAvatarInitials(username) {
  const characters = Array.from(String(username || "").trim());
  return {
    primary: (characters[0] || "U").toUpperCase(),
    secondary: (characters[1] || "").toUpperCase(),
  };
}

function applyCurrentUser(payload) {
  state.currentUser = payload.user || null;
  state.permissions = payload.permissions || [];
  const displayName = state.currentUser?.display_name || state.currentUser?.username || "User";
  const avatarInitials = getAvatarInitials(state.currentUser?.username);
  elements.userName.textContent = displayName;
  elements.userMeta.textContent = (state.currentUser?.roles || []).join(", ") || "Local account";
  elements.userAvatarPrimary.textContent = avatarInitials.primary;
  elements.userAvatarSecondary.textContent = avatarInitials.secondary;
  elements.userAvatarSecondary.hidden = !avatarInitials.secondary;
  elements.adminButton.hidden = !(
    hasPermission("auth.users.read") ||
    hasPermission("auth.roles.read") ||
    hasPermission("audit.read")
  );
}

async function refreshAuthorizationAfterFailure(status) {
  if (state.authorizationRefresh) {
    return state.authorizationRefresh;
  }
  state.authorizationRefresh = (async () => {
    if (status === 401) {
      showLogin("Your login expired. Sign in again.");
      return;
    }
    const response = await fetch("/api/auth/me");
    if (!response.ok) {
      if (response.status === 401) {
        showLogin("Your login expired. Sign in again.");
      }
      return;
    }
    const payload = await response.json().catch(() => null);
    if (!payload) {
      return;
    }
    applyCurrentUser(payload);
    if (isAdminRoute) {
      await openAdmin(state.adminTab);
    }
  })().finally(() => {
    state.authorizationRefresh = null;
  });
  return state.authorizationRefresh;
}

function resetAccountScopedState() {
  state.sessions = [];
  state.activeSessionId = "";
  state.messages = [];
  state.search = "";
  state.sending = false;
  state.activeTurn = null;
  state.pendingConfirmation = null;
  state.model = { endpoint: "", currentModel: "", models: [] };
  state.wsReady = null;
  const socket = state.ws;
  state.ws = null;
  if (socket) {
    socket.close();
  }
  elements.searchInput.value = "";
}

function showLogin(message = "") {
  resetAccountScopedState();
  state.currentUser = null;
  state.permissions = [];
  elements.appShell.hidden = true;
  elements.adminPage.hidden = true;
  elements.loginView.hidden = false;
  elements.loginError.textContent = "";
  elements.showRegisterButton.hidden = false;
  if (message) {
    elements.loginHint.textContent = message;
  }
  elements.loginPassword.value = "";
  resetPasswordVisibility(elements.loginForm);
  elements.loginUsername.focus();
}

async function showApp(mePayload) {
  resetAccountScopedState();
  applyCurrentUser(mePayload);
  elements.loginView.hidden = true;
  elements.loginHint.textContent = "Use your local ZhiCe-Agent account.";
  if (isAdminRoute) {
    elements.appShell.hidden = true;
    elements.adminPage.hidden = false;
    await openAdmin();
    return;
  }
  elements.adminPage.hidden = true;
  elements.appShell.hidden = false;
  await ensureWebSocket();
  await refreshSessions({ openFirst: true });
  await refreshModels({ showError: false });
  updateComposerState();
}

async function bootstrapAuth() {
  if (isOwnerSetupRoute) {
    elements.loginView.hidden = true;
    elements.appShell.hidden = true;
    openBootstrapDialog();
    return;
  }
  try {
    const me = await fetchCurrentUser();
    await showApp(me);
  } catch (error) {
    showLogin("Use your local ZhiCe-Agent account.");
  }
}

async function handleLogin(event) {
  event.preventDefault();
  elements.loginButton.disabled = true;
  elements.loginError.textContent = "";
  try {
    await login(elements.loginUsername.value.trim(), elements.loginPassword.value);
    const me = await fetchCurrentUser();
    await showApp(me);
  } catch (error) {
    elements.loginError.textContent = error.message;
  } finally {
    elements.loginButton.disabled = false;
  }
}

function openBootstrapDialog() {
  elements.bootstrapForm.reset();
  resetPasswordVisibility(elements.bootstrapForm);
  elements.bootstrapError.textContent = "";
  elements.bootstrapDialog.showModal();
  elements.bootstrapPassword.focus();
}

function closeBootstrapDialog() {
  elements.bootstrapDialog.close();
  elements.bootstrapPassword.value = "";
  elements.bootstrapSetupToken.value = "";
  resetPasswordVisibility(elements.bootstrapForm);
  elements.bootstrapError.textContent = "";
  if (isOwnerSetupRoute) {
    window.location.replace("/");
  }
}

async function handleBootstrap(event) {
  event.preventDefault();
  elements.bootstrapError.textContent = "";
  elements.bootstrapSubmit.disabled = true;
  try {
    await bootstrapOwner(
      elements.bootstrapSetupToken.value,
      elements.bootstrapPassword.value
    );
    window.location.replace("/");
  } catch (error) {
    elements.bootstrapError.textContent = error.message;
    if (error.code === "AUTH_ALREADY_INITIALIZED") {
      window.location.replace("/");
    }
  } finally {
    elements.bootstrapSubmit.disabled = false;
  }
}

function openRegisterDialog() {
  elements.registerForm.reset();
  resetPasswordVisibility(elements.registerForm);
  elements.registerError.textContent = "";
  elements.registerDialog.showModal();
  elements.registerUsername.focus();
}

function closeRegisterDialog() {
  elements.registerDialog.close();
  elements.registerPassword.value = "";
  elements.registerPasswordConfirm.value = "";
  resetPasswordVisibility(elements.registerForm);
  elements.registerError.textContent = "";
}

async function handleRegister(event) {
  event.preventDefault();
  elements.registerError.textContent = "";
  if (elements.registerPassword.value !== elements.registerPasswordConfirm.value) {
    elements.registerError.textContent = "Passwords do not match.";
    return;
  }
  elements.registerSubmit.disabled = true;
  try {
    await registerUser(
      elements.registerUsername.value.trim(),
      elements.registerPassword.value
    );
    const me = await fetchCurrentUser();
    closeRegisterDialog();
    await showApp(me);
  } catch (error) {
    elements.registerError.textContent = error.message;
  } finally {
    elements.registerSubmit.disabled = false;
  }
}

async function handleLogout() {
  try {
    await logout();
  } finally {
    elements.userMenu.hidden = true;
    showLogin("Signed out.");
  }
}

function setFormStatus(element, message = "", stateName = "") {
  element.textContent = message;
  element.dataset.state = stateName;
}

function initializePasswordToggles(root = document) {
  for (const button of root.querySelectorAll("[data-password-toggle]")) {
    if (button.dataset.passwordToggleReady === "true") {
      continue;
    }
    const targetId = button.dataset.passwordToggle;
    const input = root.querySelector(`[id="${targetId}"]`) || document.getElementById(targetId);
    if (!input) {
      continue;
    }
    button.dataset.passwordToggleReady = "true";
    button.dataset.hiddenLabel = button.getAttribute("aria-label") || "Show password";
    button.addEventListener("click", () => {
      const shouldReveal = input.type === "password";
      input.type = shouldReveal ? "text" : "password";
      button.setAttribute("aria-pressed", shouldReveal ? "true" : "false");
      button.setAttribute(
        "aria-label",
        shouldReveal
          ? button.dataset.hiddenLabel.replace(/^Show\s+/, "Hide ")
          : button.dataset.hiddenLabel
      );
      input.focus();
    });
  }
}

function resetPasswordVisibility(root = document) {
  for (const button of root.querySelectorAll("[data-password-toggle]")) {
    const targetId = button.dataset.passwordToggle;
    const input = root.querySelector(`[id="${targetId}"]`) || document.getElementById(targetId);
    if (input) {
      input.type = "password";
    }
    button.setAttribute("aria-pressed", "false");
    button.setAttribute("aria-label", button.dataset.hiddenLabel || "Show password");
  }
}

function openAccountSettings() {
  elements.userMenu.hidden = true;
  elements.accountUsername.value = state.currentUser?.username || "";
  elements.accountDisplayName.value = state.currentUser?.display_name || "";
  elements.passwordForm.reset();
  resetPasswordVisibility(elements.passwordForm);
  setFormStatus(elements.profileStatus);
  setFormStatus(elements.passwordStatus);
  elements.accountDialog.showModal();
}

async function handleProfileUpdate(event) {
  event.preventDefault();
  const button = elements.profileForm.querySelector('button[type="submit"]');
  button.disabled = true;
  setFormStatus(elements.profileStatus);
  try {
    const payload = await updateCurrentProfile(elements.accountDisplayName.value.trim());
    applyCurrentUser(payload);
    elements.accountDisplayName.value = state.currentUser?.display_name || "";
    setFormStatus(elements.profileStatus, "Profile updated.", "success");
  } catch (error) {
    setFormStatus(elements.profileStatus, error.message, "error");
  } finally {
    button.disabled = false;
  }
}

async function handlePasswordChange(event) {
  event.preventDefault();
  const button = elements.passwordForm.querySelector('button[type="submit"]');
  const currentPassword = elements.currentPassword.value;
  const newPassword = elements.newPassword.value;
  if (newPassword !== elements.confirmPassword.value) {
    setFormStatus(elements.passwordStatus, "New passwords do not match.", "error");
    return;
  }
  button.disabled = true;
  setFormStatus(elements.passwordStatus);
  try {
    const username = state.currentUser?.username || "";
    await changeCurrentPassword(currentPassword, newPassword);
    elements.passwordForm.reset();
    elements.accountDialog.close();
    elements.userMenu.hidden = true;
    showLogin("Password changed. Sign in again with your new password.");
    elements.loginUsername.value = username;
    elements.loginPassword.focus();
  } catch (error) {
    setFormStatus(elements.passwordStatus, error.message, "error");
  } finally {
    button.disabled = false;
  }
}

async function openAdmin(tab = "users") {
  const tabPermissions = {
    users: "auth.users.read",
    roles: "auth.roles.read",
    audit: "audit.read",
  };
  const allowedTabs = Object.keys(tabPermissions).filter((name) => hasPermission(tabPermissions[name]));
  elements.adminPage.hidden = false;
  elements.managementBody.replaceChildren();
  if (!allowedTabs.length) {
    elements.managementBody.replaceChildren(
      createErrorNode("You do not have permission to access administration.")
    );
    return;
  }
  if (!allowedTabs.includes(tab)) {
    tab = allowedTabs[0];
  }
  state.adminTab = tab;
  elements.userMenu.hidden = true;
  for (const button of document.querySelectorAll("[data-admin-tab]")) {
    button.hidden = !hasPermission(tabPermissions[button.dataset.adminTab]);
    button.classList.toggle("is-active", button.dataset.adminTab === tab);
  }
  elements.managementBody.replaceChildren(createLoadingNode());
  try {
    if (tab === "users") {
      await renderAdminUsers();
    } else if (tab === "roles") {
      await renderAdminRoles();
    } else {
      await renderAuditEvents();
    }
  } catch (error) {
    elements.managementBody.replaceChildren(createErrorNode(error.message));
  }
}

async function renderAdminUsers() {
  const payload = await fetchAdminUsers();
  const wrap = document.createElement("div");
  const currentIsOwner = (state.currentUser?.roles || []).includes("owner");
  const canManageAdmins = hasPermission("auth.admin.manage");
  if (hasPermission("auth.users.manage")) {
    const form = document.createElement("form");
    form.className = "admin-form";
    form.innerHTML = [
      '<label>Username <input name="username" required /></label>',
      '<label>Display name <input name="display_name" placeholder="Defaults to username" /></label>',
      '<label>Password <span class="password-input-wrap"><input id="adminCreatePassword" name="password" type="password" minlength="8" required /><button class="password-toggle" type="button" tabindex="-1" data-password-toggle="adminCreatePassword" aria-controls="adminCreatePassword" aria-label="Show password" aria-pressed="false"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6"></path><circle cx="12" cy="12" r="2.5"></circle></svg></button></span></label>',
      `<label>Role <select name="role"><option>viewer</option><option>developer</option><option>auditor</option>${canManageAdmins ? "<option>admin</option>" : ""}</select></label>`,
      '<button class="primary-button" type="submit">Create user</button>',
    ].join("");
    initializePasswordToggles(form);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const data = new FormData(form);
      await createAdminUser({
        username: data.get("username"),
        display_name: data.get("display_name"),
        password: data.get("password"),
        roles: [data.get("role")],
      });
      await renderAdminUsers();
    });
    wrap.append(form);
  }
  const table = document.createElement("table");
  table.innerHTML = "<thead><tr><th>User</th><th>Status</th><th>Roles</th><th></th></tr></thead>";
  const body = document.createElement("tbody");
  for (const user of payload.users || []) {
    const row = document.createElement("tr");
    const identity = document.createElement("td");
    identity.textContent = `${user.display_name} (${user.username})`;
    const status = document.createElement("td");
    status.textContent = user.status;
    const roles = document.createElement("td");
    roles.textContent = (user.roles || []).join(", ");
    const actions = document.createElement("td");
    const isOwner = (user.roles || []).includes("owner");
    const isAdmin = (user.roles || []).includes("admin");
    const canOperateTarget = !isOwner && (!isAdmin || canManageAdmins);
    if (hasPermission("auth.users.manage") && user.id !== state.currentUser?.id && canOperateTarget) {
      const toggle = document.createElement("button");
      toggle.type = "button";
      toggle.textContent = user.status === "active" ? "Disable" : "Enable";
      toggle.addEventListener("click", async () => {
        await updateAdminUser(user.id, { status: user.status === "active" ? "disabled" : "active" });
        await renderAdminUsers();
      });
      actions.append(toggle);
    }
    if (canManageAdmins && !isOwner && user.id !== state.currentUser?.id) {
      const roleToggle = document.createElement("button");
      roleToggle.type = "button";
      roleToggle.textContent = isAdmin ? "Remove admin" : "Make admin";
      roleToggle.addEventListener("click", async () => {
        const roles = isAdmin
          ? (user.roles || []).filter((role) => role !== "admin")
          : ["admin"];
        await updateAdminUser(user.id, { roles: roles.length ? roles : ["viewer"] });
        await renderAdminUsers();
      });
      actions.append(roleToggle);
    }
    if (currentIsOwner && isAdmin) {
      const delegation = document.createElement("button");
      delegation.type = "button";
      delegation.textContent = user.can_manage_admins
        ? "Revoke admin management"
        : "Allow admin management";
      delegation.addEventListener("click", async () => {
        await updateAdminUser(user.id, { can_manage_admins: !user.can_manage_admins });
        await renderAdminUsers();
      });
      actions.append(delegation);
    }
    row.append(identity, status, roles, actions);
    body.append(row);
  }
  table.append(body);
  wrap.append(table);
  elements.managementBody.replaceChildren(wrap);
}

async function renderAdminRoles() {
  const payload = await fetchAdminRoles();
  const wrap = document.createElement("div");
  for (const role of payload.roles || []) {
    const protectedRole = ["owner", "admin"].includes(role.key);
    const section = document.createElement("section");
    section.className = "audit-event";
    const heading = document.createElement("h3");
    heading.textContent = `${role.name} (${role.key})`;
    section.append(heading);
    const grid = document.createElement("div");
    grid.className = "permission-grid";
    for (const permission of payload.permissions || []) {
      const label = document.createElement("label");
      const input = document.createElement("input");
      input.type = "checkbox";
      input.value = permission;
      input.checked = (role.permission_keys || []).includes(permission);
      input.disabled = protectedRole || !hasPermission("auth.roles.manage");
      label.append(input, document.createTextNode(permission));
      grid.append(label);
    }
    section.append(grid);
    if (hasPermission("auth.roles.manage") && !protectedRole) {
      const save = document.createElement("button");
      save.type = "button";
      save.className = "primary-button";
      save.textContent = "Save permissions";
      save.addEventListener("click", async () => {
        const selected = [...grid.querySelectorAll("input:checked")].map((input) => input.value);
        await updateAdminRole(role.id, selected);
        await renderAdminRoles();
      });
      section.append(save);
    }
    wrap.append(section);
  }
  elements.managementBody.replaceChildren(wrap);
}

async function renderAuditEvents() {
  const payload = await fetchAuditEvents();
  const wrap = document.createElement("div");
  for (const event of payload.events || []) {
    const node = document.createElement("div");
    node.className = "audit-event";
    node.textContent = [
      `${event.ts}  ${event.action}`,
      `actor=${event.actor_user_id || "system"} session=${event.session_id || "-"} turn=${event.turn_id || "-"}`,
      `decision=${event.decision || "-"} reason=${event.reason_code || "-"}`,
    ].join("\n");
    wrap.append(node);
  }
  if (!wrap.childNodes.length) {
    wrap.append(document.createTextNode("No audit events."));
  }
  elements.managementBody.replaceChildren(wrap);
}

function showToolConfirmation(confirmation) {
  state.pendingConfirmation = confirmation;
  elements.confirmationTitle.textContent = confirmation.confirmation_title || "Confirm tool action";
  elements.confirmationDetails.replaceChildren();
  const customFields = Array.isArray(confirmation.confirmation_fields)
    ? confirmation.confirmation_fields.map((item) => [item.label, item.value])
    : [];
  const fields = [
    ["Tool", confirmation.tool_name],
    ["Risk", `${confirmation.risk_level} / ${confirmation.risk_category || ""}`],
    ...customFields,
    ["Permission", confirmation.permission_key || ""],
    ["Session", confirmation.session_id || state.activeSessionId],
    ["Turn", confirmation.turn_id || ""],
    ["Expires", confirmation.expires_at || ""],
  ];
  for (const [label, value] of fields) {
    const term = document.createElement("dt");
    term.textContent = label;
    const detail = document.createElement("dd");
    detail.textContent = String(value || "-");
    elements.confirmationDetails.append(term, detail);
  }
  if (!elements.confirmationDialog.open) {
    elements.confirmationDialog.showModal();
  }
}

async function handleConfirmation(approve) {
  const confirmation = state.pendingConfirmation;
  if (!confirmation?.confirmation_id) {
    return;
  }
  elements.confirmationApprove.disabled = true;
  elements.confirmationDeny.disabled = true;
  try {
    await decideConfirmation(confirmation.confirmation_id, approve);
  } catch (error) {
    showTransientError(error.message);
  } finally {
    state.pendingConfirmation = null;
    elements.confirmationApprove.disabled = false;
    elements.confirmationDeny.disabled = false;
    elements.confirmationDialog.close();
  }
}

function createLoadingNode() {
  const node = document.createElement("div");
  node.className = "muted";
  node.textContent = "Loading…";
  return node;
}

function createErrorNode(message) {
  const node = document.createElement("div");
  node.className = "form-error";
  node.textContent = message;
  return node;
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

elements.loginForm.addEventListener("submit", handleLogin);
elements.showRegisterButton.addEventListener("click", openRegisterDialog);
elements.bootstrapClose.addEventListener("click", closeBootstrapDialog);
elements.bootstrapCancel.addEventListener("click", closeBootstrapDialog);
elements.bootstrapForm.addEventListener("submit", handleBootstrap);
elements.registerClose.addEventListener("click", closeRegisterDialog);
elements.registerCancel.addEventListener("click", closeRegisterDialog);
elements.registerForm.addEventListener("submit", handleRegister);
elements.userEntry.addEventListener("click", () => {
  elements.userMenu.hidden = !elements.userMenu.hidden;
});
elements.accountSettingsButton.addEventListener("click", openAccountSettings);
elements.logoutButton.addEventListener("click", handleLogout);
elements.adminButton.addEventListener("click", () => {
  window.location.assign("/admin");
});
elements.managementClose.addEventListener("click", () => window.location.assign("/"));
elements.adminLogoutButton.addEventListener("click", handleLogout);
elements.accountClose.addEventListener("click", () => elements.accountDialog.close());
elements.profileForm.addEventListener("submit", handleProfileUpdate);
elements.passwordForm.addEventListener("submit", handlePasswordChange);
elements.confirmationApprove.addEventListener("click", () => handleConfirmation(true));
elements.confirmationDeny.addEventListener("click", () => handleConfirmation(false));
for (const button of document.querySelectorAll("[data-admin-tab]")) {
  button.addEventListener("click", () => openAdmin(button.dataset.adminTab));
}

initializePasswordToggles();
bootstrapAuth();
