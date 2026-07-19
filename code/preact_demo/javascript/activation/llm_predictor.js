const { tokenize, unique } = require("../common/text");

function recentDialogueSummary(history, windowSize = 2) {
  return history
    .slice(-windowSize)
    .map((turn) => `${turn.speaker}: ${turn.text}`)
    .join("\n");
}

function activeSignals(history) {
  const recent = recentDialogueSummary(history);
  const recentTokens = new Set(tokenize(recent));
  const activeSegments = unique(history.slice(-2).map((turn) => turn.segment_id));
  const activeEntities = unique(
    history
      .slice(-2)
      .flatMap((turn) => (turn.memories || []).flatMap((memory) => memory.entities || []))
  );

  return { activeEntities, activeSegments, recent, recentTokens };
}

function llmPredictActivation({ history, memoryNodes, budget }) {
  const signals = activeSignals(history);
  const maxTimestamp = Math.max(...memoryNodes.map((memory) => memory.timestamp));

  const ranked = memoryNodes
    .map((memory) => {
      const memoryTokens = tokenize([
        memory.summary,
        memory.content,
        ...(memory.keywords || []),
        ...(memory.entities || [])
      ].join(" "));
      const keywordHits = memoryTokens.filter((token) => signals.recentTokens.has(token)).length;
      const entityHits = (memory.entities || []).filter((entity) =>
        signals.activeEntities.includes(entity)
      ).length;
      const segmentHit = signals.activeSegments.includes(memory.segment_id) ? 1 : 0;
      const recency = memory.timestamp / maxTimestamp;
      const score =
        keywordHits * 0.18 +
        entityHits * 0.25 +
        segmentHit * 0.28 +
        memory.importance * 0.3 +
        recency * 0.07;

      return {
        id: memory.id,
        reason: `Simulated LLM score from recent keyword/entity overlap, importance, and recency.`,
        confidence: Number(Math.min(0.99, score).toFixed(3)),
        score
      };
    })
    .sort((a, b) => b.score - a.score)
    .slice(0, budget);

  return {
    predicted_future_intents: [
      "The user may continue the active segment.",
      "The user may ask for decisions, metrics, or comparisons mentioned recently."
    ],
    activated_memory_ids: ranked.map(({ id, reason, confidence }) => ({ id, reason, confidence }))
  };
}

module.exports = {
  llmPredictActivation,
  recentDialogueSummary
};
