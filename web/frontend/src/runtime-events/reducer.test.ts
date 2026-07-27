import { describe, expect, it } from "vitest";

import type { WsEnvelope } from "@/api/types";
import { applyRuntimeEvent, emptyRuntimeState } from "./reducer";

function event(name: string, sequence: number, extras: Record<string, unknown> = {}): WsEnvelope {
  return { event: "runtime_event", session_id: "s1", turn_id: "t1", data: { type: name, sequence, turn_id: "t1", session_id: "s1", display: { title: name }, ...extras } };
}

describe("RuntimeEvent reducer", () => {
  it("ignores stale events and clears terminal state", () => {
    const started = applyRuntimeEvent(emptyRuntimeState(), event("llm.started", 4), "t1");
    const stale = applyRuntimeEvent(started, event("context.started", 3), "t1");
    const completed = applyRuntimeEvent(stale, event("turn.completed", 5), "t1");
    expect(stale).toEqual(started);
    expect(completed).toEqual(emptyRuntimeState());
  });

  it("tracks child sequences independently", () => {
    const child = (taskId: string, sequence: number, type: string) => ({
      event: "runtime_event", session_id: `child-${taskId}`, data: {
        type, sequence, session_id: `child-${taskId}`, turn_id: `turn-${taskId}`,
        root_session_id: "s1", root_turn_id: "t1", task_id: taskId,
        display: { title: type },
      },
    } as WsEnvelope);
    const first = applyRuntimeEvent(emptyRuntimeState(), child("implementation", 3, "tool.started"), "t1", "s1");
    const second = applyRuntimeEvent(first, child("tests", 1, "llm.started"), "t1", "s1");
    const done = applyRuntimeEvent(second, child("implementation", 4, "turn.completed"), "t1", "s1");
    expect(Object.keys(second.childTasks)).toEqual(["implementation", "tests"]);
    expect(done.childTasks.implementation).toBeUndefined();
    expect(done.childTasks.tests.title).toBe("llm.started");
  });
});
