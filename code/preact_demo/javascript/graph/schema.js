const NODE_TYPES = {
  MEMORY: "MemoryNode",
  TURN: "TurnNode",
  SEGMENT: "SegmentNode",
  ENTITY: "EntityNode"
};

const EDGE_TYPES = {
  DERIVED_FROM: "derived_from",
  BELONGS_TO: "belongs_to",
  MENTIONS: "mentions",
  SIMILAR_TO: "similar_to",
  TEMPORAL_NEXT: "temporal_next"
};

module.exports = {
  EDGE_TYPES,
  NODE_TYPES
};
