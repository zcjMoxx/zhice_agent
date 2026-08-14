import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import type { TravelEvidence } from "@/api/types";
import TravelSourcesDrawer from "./TravelSourcesDrawer.vue";

describe("TravelSourcesDrawer", () => {
  it("shows freshness and blocks non-http source navigation", async () => {
    const wrapper = mount(TravelSourcesDrawer, {
      attachTo: document.body,
      props: { evidence: [evidence("javascript:alert(1)")] },
    });
    await wrapper.find("button").trigger("click");

    expect(document.body.textContent).toContain("snapshot");
    expect(document.body.querySelector('.travel-source-list a')).toBeNull();
    wrapper.unmount();
  });
});

function evidence(url: string): TravelEvidence {
  return {
    evidence_id: "ev-1",
    source_type: "social_post",
    provider: "xhs-readonly",
    title: "个体体验",
    source_url: url,
    published_at: "",
    retrieved_at: "2026-09-28T00:00:00Z",
    data_as_of: "",
    excerpt: "只显示短摘录",
    facts: [],
    confidence: 0.5,
    freshness: "snapshot",
    content_hash: "a".repeat(64),
  };
}

