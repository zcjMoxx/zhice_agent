(function attachRuntimeEventState(globalScope) {
  "use strict";

  function applyRuntimeEvent(activeTurn, pendingMessage, envelope) {
    const runtimeEvent = envelope?.data || {};
    const rootSessionId = String(
      runtimeEvent.root_session_id || runtimeEvent.parent_session_id || envelope?.session_id || ""
    );
    if (!activeTurn || !pendingMessage || rootSessionId !== activeTurn.sessionId) {
      return false;
    }
    const turnId = String(
      runtimeEvent.root_turn_id || runtimeEvent.parent_turn_id || runtimeEvent.turn_id || envelope.turn_id || ""
    );
    if (activeTurn.turnId && turnId && turnId !== activeTurn.turnId) {
      return false;
    }
    if (!activeTurn.turnId && turnId) {
      activeTurn.turnId = turnId;
    }
    const taskId = String(runtimeEvent.task_id || "");
    const agentId = String(runtimeEvent.agent_id || "parent");
    const streamKey = taskId ? `${agentId}:${taskId}` : agentId;
    const sequence = Number(runtimeEvent.sequence || 0);
    const sequences = { ...(pendingMessage.runtimeSequences || {}) };
    if (!Number.isInteger(sequence) || sequence <= (sequences[streamKey] || 0)) {
      return false;
    }
    sequences[streamKey] = sequence;
    pendingMessage.runtimeSequences = sequences;
    if (!taskId) {
      pendingMessage.runtimeSequence = sequence;
    }
    pendingMessage.runtimeTurnId = turnId;
    pendingMessage.runtimeEvents = [...(pendingMessage.runtimeEvents || []), runtimeEvent].slice(-12);
    if (taskId) {
      const tasks = { ...(pendingMessage.subagentTasks || {}) };
      const terminal = ["turn.completed", "turn.failed", "turn.stopped"].includes(runtimeEvent.type);
      tasks[taskId] = {
        taskId,
        title: String(runtimeEvent.display?.title || tasks[taskId]?.title || taskId),
        status: terminal ? runtimeEvent.type.split(".")[1] : "running",
      };
      pendingMessage.subagentTasks = tasks;
      const values = Object.values(tasks);
      const completed = values.filter((task) => task.status !== "running").length;
      pendingMessage.runtimeStatus = `并行子任务 ${completed}/${values.length} 已完成`;
    } else if (["turn.completed", "turn.failed", "turn.stopped"].includes(runtimeEvent.type)) {
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
