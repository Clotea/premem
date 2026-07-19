function generateAnswer(query, memories) {
  if (!memories.length) {
    return `No verified memory was found for: ${query}`;
  }

  const context = memories.map((memory) => memory.summary).join(" ");
  return `Based on memory: ${context}`;
}

module.exports = {
  generateAnswer
};
