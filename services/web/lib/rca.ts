import type { TopologyEdgeType } from "./types";

/** A slightly more natural in-UI phrase than the raw Cypher relationship
 * name, without touching what the API returns -- `text` on an
 * RcaSuggestion still uses "RUNS_ON"/"SERVES" verbatim (that's what
 * makes the sentence satisfy the "references the graph relationship"
 * acceptance criterion), this is only for standalone badges/labels in
 * the UI. */
const RELATIONSHIP_LABEL: Record<TopologyEdgeType, string> = {
  RUNS_ON: "runs on",
  SERVES: "serves",
  CONNECTS: "connects to",
};

export function relationshipLabel(relationship: TopologyEdgeType): string {
  return RELATIONSHIP_LABEL[relationship] ?? relationship.toLowerCase().replace(/_/g, " ");
}
