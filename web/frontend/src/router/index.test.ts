import { describe, expect, it } from "vitest";

import router from "./index";

describe("workflow routes", () => {
  it("keeps the workflow overview and editor detail on distinct URLs", () => {
    expect(router.resolve("/workflows")).toMatchObject({ name: "workflows", params: {} });
    expect(router.resolve("/workflows/workflow-123")).toMatchObject({
      name: "workflow-detail",
      params: { workflowId: "workflow-123" },
    });
  });
});
