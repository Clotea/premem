const { localSubgraph } = require("../graph/build_graph");

function buildWorkingCache({ cacheId, budget, prediction, memoryNodes, graph }) {
  const memoryById = new Map(memoryNodes.map((memory) => [memory.id, memory]));
  const memoryIds = prediction.activated_memory_ids.map((item) => item.id);
  const summaries = memoryIds.map((id) => memoryById.get(id).summary);

  return {
    cache_id: cacheId,
    budget,
    memory_ids: memoryIds,
    summaries,
    local_subgraph: localSubgraph(graph, memoryIds),
    prediction
  };
}

module.exports = {
  buildWorkingCache
};
