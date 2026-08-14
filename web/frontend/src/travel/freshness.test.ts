import { describe, expect, it } from "vitest";

import type { TravelEvidence } from "@/api/types";
import { needsTravelRefresh } from "./freshness";

describe("travel freshness TTL", () => {
  it("distinguishes expired live data, historical references, estimates and unknowns", () => {
    const now = Date.parse("2026-10-01T12:00:00Z");
    expect(needsTravelRefresh(item("live", "2026-10-01T00:00:00Z"), now)).toBe(true);
    expect(needsTravelRefresh(item("live", "2026-10-01T10:00:00Z"), now)).toBe(false);
    expect(needsTravelRefresh(item("historical", "2020-10-01T00:00:00Z"), now)).toBe(false);
    expect(needsTravelRefresh(item("estimate", "2026-09-01T00:00:00Z"), now)).toBe(true);
    expect(needsTravelRefresh(item("unknown", ""), now)).toBe(true);
  });
});

function item(freshness: TravelEvidence["freshness"], dataAsOf: string): TravelEvidence {
  return {
    evidence_id: "ev", source_type: freshness === "estimate" ? "model_estimate" : "official_api",
    provider: "fixture", title: "fixture", source_url: "https://example.com", published_at: "",
    retrieved_at: dataAsOf, data_as_of: dataAsOf, excerpt: "", facts: [], confidence: 0.5,
    freshness, content_hash: "a".repeat(64),
  };
}
