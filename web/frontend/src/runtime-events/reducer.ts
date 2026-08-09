import type { RuntimeEventData, RuntimeUiState, WsEnvelope } from "@/api/types";

const TERMINAL_EVENTS = new Set(["turn.completed", "turn.stopped", "turn.failed", "turn.error"]);

export function emptyRuntimeState(): RuntimeUiState {
  return { sequence: 0, title: "", phase: "", status: "", childTasks: {} };
}

export function applyRuntimeEvent(
  state: RuntimeUiState,
  envelope: WsEnvelope,
  activeTurnId = "",
  activeSessionId = "",
): RuntimeUiState {
  const event = (envelope.data ?? {}) as RuntimeEventData;
  if (event.display?.visibility === "internal") return state;
  const sessionId = String(event.root_session_id || event.session_id || envelope.session_id || "");
  const turnId = String(event.root_turn_id || event.turn_id || envelope.turn_id || "");
  if (activeSessionId && sessionId && sessionId !== activeSessionId) return state;
  if (activeTurnId && turnId && turnId !== activeTurnId) return state;

  const sequence = Number(event.sequence ?? 0);
  const scope = event.scope ?? {};
  const childKey = event.task_id || event.agent_id || scope.task_id || scope.agent_id || "";
  const eventName = String(event.type ?? event.event ?? "");
  const title = String(
    eventName === "skill.progress"
      ? event.display?.detail ?? event.display?.title ?? ""
      : event.display?.title ?? event.display?.detail ?? "",
  );
  const status = String(event.status ?? "");

  if (childKey) {
    const previous = state.childTasks[childKey];
    if (previous && sequence <= previous.sequence) return state;
    const childTasks = { ...state.childTasks };
    if (TERMINAL_EVENTS.has(eventName)) delete childTasks[childKey];
    else childTasks[childKey] = { sequence, title: title || scope.task_name || childKey, status };
    return { ...state, childTasks };
  }

  if (sequence <= state.sequence) return state;
  if (TERMINAL_EVENTS.has(eventName)) return emptyRuntimeState();
  return { ...state, sequence, title, phase: eventName, status };
}
