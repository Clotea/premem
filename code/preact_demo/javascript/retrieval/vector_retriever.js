const { overlapScore } = require("../common/text");

function vectorRetrieve(query, memoryNodes, topK) {
  return memoryNodes
    .map((memory) => ({ memory, score: overlapScore(query, memory) }))
    .sort((a, b) => b.score - a.score)
    .slice(0, topK)
    .map((item) => item.memory);
}

module.exports = {
  vectorRetrieve
};
