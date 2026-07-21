(function attachRuntimeEventState(globalScope) {
  "use strict";

  function applyRuntimeEvent(activeTurn, pendingMessage, envelope) {
    const runtimeEvent = envelope?.data || {};
    if (!activeTurn || !pendingMessage || envelope?.session_id !== activeTurn.sessionId) {
      return false;
    }
    const turnId = String(runtimeEvent.turn_id || envelope.turn_id || "");
    if (activeTurn.turnId && turnId && turnId !== activeTurn.turnId) {
      return false;
    }
    if (!activeTurn.turnId && turnId) {
      activeTurn.turnId = turnId;
    }
    const sequence = Number(runtimeEvent.sequence || 0);
    if (!Number.isInteger(sequence) || sequence <= (pendingMessage.runtimeSequence || 0)) {
      return false;
    }
    pendingMessage.runtimeSequence = sequence;
    pendingMessage.runtimeTurnId = turnId;
    pendingMessage.runtimeEvents = [...(pendingMessage.runtimeEvents || []), runtimeEvent].slice(-12);
    if (["turn.completed", "turn.failed", "turn.stopped"].includes(runtimeEvent.type)) {
      pendingMessage.runtimeStatus = "";
    } else {
      const display = runtimeEvent.display || {};
      pendingMessage.runtimeStatus = String(display.title || "");
    }
    return true;
  }

  const api = { applyRuntimeEvent };
  globalScope.ZhiCeRuntimeEventState = api;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : window);
