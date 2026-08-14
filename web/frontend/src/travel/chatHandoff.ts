const CHAT_HANDOFF_KEY = "zhice.chat.handoff.v1";
const MAX_HANDOFF_CHARS = 2000;

export function saveChatHandoff(question: string): boolean {
  const normalized = String(question || "").trim().slice(0, MAX_HANDOFF_CHARS);
  if (!normalized) return false;
  try {
    sessionStorage.setItem(CHAT_HANDOFF_KEY, normalized);
    return true;
  } catch {
    return false;
  }
}

export function consumeChatHandoff(): string {
  try {
    const value = String(sessionStorage.getItem(CHAT_HANDOFF_KEY) || "").trim().slice(0, MAX_HANDOFF_CHARS);
    sessionStorage.removeItem(CHAT_HANDOFF_KEY);
    return value;
  } catch {
    return "";
  }
}

export { CHAT_HANDOFF_KEY };
