import type { TravelEvidence } from "@/api/types";

const TTL_MILLISECONDS: Record<string, number> = {
  live: 6 * 60 * 60 * 1000,
  snapshot: 30 * 24 * 60 * 60 * 1000,
  estimate: 7 * 24 * 60 * 60 * 1000,
};

export function needsTravelRefresh(evidence: TravelEvidence, now = Date.now()): boolean {
  if (evidence.freshness === "unknown") return true;
  if (evidence.freshness === "historical") return false;
  const ttl = TTL_MILLISECONDS[evidence.freshness];
  if (!ttl) return true;
  const timestamp = Date.parse(evidence.data_as_of || evidence.retrieved_at);
  return !Number.isFinite(timestamp) || timestamp > now + 5 * 60 * 1000 || now - timestamp > ttl;
}

