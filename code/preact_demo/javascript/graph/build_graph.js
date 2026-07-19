const { EDGE_TYPES, NODE_TYPES } = require("./schema");
const { jaccard, tokenize } = require("../common/text");

function addNode(graph, id, type, data) {
  if (!graph.nodes[id]) {
    graph.nodes[id] = { id, type, ...data };
  }
}

function addEdge(graph, from, to, type) {
  graph.edges.push({ from, to, type });
}

function buildHeterogeneousGraph(memoryNodes, history, options = {}) {
  const similarityThreshold = options.similarityThreshold || 0.28;
  const graph = { nodes: {}, edges: [] };

  for (const turn of history) {
    addNode(graph, turn.id, NODE_TYPES.TURN, {
      speaker: turn.speaker,
      text: turn.text,
      timestamp: turn.timestamp,
      segment_id: turn.segment_id
    });
    addNode(graph, turn.segment_id, NODE_TYPES.SEGMENT, {
      summary: turn.segment_summary
    });
    addEdge(graph, turn.id, turn.segment_id, EDGE_TYPES.BELONGS_TO);
  }

  for (const memory of memoryNodes) {
    addNode(graph, memory.id, NODE_TYPES.MEMORY, memory);
    addEdge(graph, memory.id, memory.source_turn_id, EDGE_TYPES.DERIVED_FROM);
    addEdge(graph, memory.id, memory.segment_id, EDGE_TYPES.BELONGS_TO);

    for (const entity of memory.entities || []) {
      const entityId = `e_${entity.toLowerCase().replace(/[^a-z0-9]+/g, "_")}`;
      addNode(graph, entityId, NODE_TYPES.ENTITY, { name: entity });
      addEdge(graph, memory.id, entityId, EDGE_TYPES.MENTIONS);
    }
  }

  const ordered = [...memoryNodes].sort((a, b) => a.timestamp - b.timestamp);
  for (let i = 0; i < ordered.length - 1; i += 1) {
    addEdge(graph, ordered[i].id, ordered[i + 1].id, EDGE_TYPES.TEMPORAL_NEXT);
  }

  for (let i = 0; i < memoryNodes.length; i += 1) {
    for (let j = i + 1; j < memoryNodes.length; j += 1) {
      const left = tokenize(`${memoryNodes[i].summary} ${(memoryNodes[i].keywords || []).join(" ")}`);
      const right = tokenize(`${memoryNodes[j].summary} ${(memoryNodes[j].keywords || []).join(" ")}`);
      if (jaccard(left, right) >= similarityThreshold) {
        addEdge(graph, memoryNodes[i].id, memoryNodes[j].id, EDGE_TYPES.SIMILAR_TO);
      }
    }
  }

  return graph;
}

function summarizeGraph(graph) {
  return {
    node_count: Object.keys(graph.nodes).length,
    edge_count: graph.edges.length,
    memory_ids: Object.values(graph.nodes)
      .filter((node) => node.type === NODE_TYPES.MEMORY)
      .map((node) => node.id)
  };
}

function localSubgraph(graph, memoryIds) {
  const keepNodes = new Set(memoryIds);
  const keepEdges = [];

  for (const edge of graph.edges) {
    if (memoryIds.includes(edge.from) || memoryIds.includes(edge.to)) {
      keepEdges.push(edge);
      keepNodes.add(edge.from);
      keepNodes.add(edge.to);
    }
  }

  return {
    nodes: [...keepNodes].map((id) => graph.nodes[id]).filter(Boolean),
    edges: keepEdges
  };
}

module.exports = {
  buildHeterogeneousGraph,
  localSubgraph,
  summarizeGraph
};
