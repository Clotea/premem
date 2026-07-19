const { overlapScore } = require("../common/text");

function verifyCache({ query, cache, memoryNodes, threshold }) {
  const memoryById = new Map(memoryNodes.map((memory) => [memory.id, memory]));
  const selected = cache.memory_ids
    .map((id) => {
      const memory = memoryById.get(id);
      return { memory, score: overlapScore(query, memory) };
    })
    .filter((item) => item.score >= threshold)
    .sort((a, b) => b.score - a.score);

  return {
    use_cache: selected.length > 0,
    sufficient: selected.length > 0,
    selected_memory_ids: selected.map((item) => item.memory.id),
    memories: selected.map((item) => item.memory),
    scores: selected.map((item) => ({ id: item.memory.id, score: Number(item.score.toFixed(3)) }))
  };
}

module.exports = {
  verifyCache
};
