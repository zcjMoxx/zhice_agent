import { describe, expect, it } from "vitest";

import { PERMISSION_META, permissionLabel } from "./permissions";

const BUILTIN_KEYS = [
  "auth.users.read", "auth.users.manage", "auth.admin.manage", "auth.roles.read",
  "auth.roles.manage", "session.manage.any", "chat.stop.any", "turn.read.any",
  "tool.exec.dangerous", "skill.sync", "audit.read", "audit.export",
];

describe("permission presentation", () => {
  it("maps every built-in permission to Chinese capability metadata", () => {
    expect(Object.keys(PERMISSION_META).sort()).toEqual([...BUILTIN_KEYS].sort());
    for (const key of BUILTIN_KEYS) {
      expect(PERMISSION_META[key].name).toMatch(/[\u3400-\u9fff]/u);
      expect(PERMISSION_META[key].group).not.toBe("");
    }
  });

  it("falls back to unknown technical keys", () => {
    expect(permissionLabel("future.capability")).toBe("future.capability");
  });
});
