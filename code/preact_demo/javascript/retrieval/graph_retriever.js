const { overlapScore } = require("../common/text");

function graphRetrieve(query, graph, memoryNodes, topK) {
  const neighborBoosts = new Map();

  for (const edge of graph.edges) {
    if (edge.type === "similar_to" || edge.type === "temporal_next") {
      neighborBoosts.set(edge.from, (neighborBoosts.get(edge.from) || 0) + 0.03);
      neighborBoosts.set(edge.to, (neighborBoosts.get(edge.to) || 0) + 0.03);
    }
  }

  return memoryNodes
    .map((memory) => ({
      memory,
      score: overlapScore(query, memory) + (neighborBoosts.get(memory.id) || 0)
    }))
    .sort((a, b) => b.score - a.score)
    .slice(0, topK)
    .map((item) => item.memory);
}

module.exports = {
  graphRetrieve
};
