const { estimateTokens, tokenize } = require("../common/text");

function computeActivationMetrics(activated, evidence) {
  const activatedSet = new Set(activated);
  const evidenceSet = new Set(evidence);
  const intersection = [...activatedSet].filter((id) => evidenceSet.has(id));
  const precision = activatedSet.size ? intersection.length / activatedSet.size : 0;
  const recall = evidenceSet.size ? intersection.length / evidenceSet.size : 0;

  return {
    precision,
    recall,
    hit_rate: intersection.length > 0 ? 1 : 0,
    wasted_rate: 1 - precision
  };
}

function f1Score(prediction, gold) {
  const predTokens = tokenize(prediction);
  const goldTokens = tokenize(gold);
  const predCounts = counts(predTokens);
  const goldCounts = counts(goldTokens);
  let overlap = 0;

  for (const [token, count] of predCounts.entries()) {
    overlap += Math.min(count, goldCounts.get(token) || 0);
  }

  if (overlap === 0) return 0;
  const precision = overlap / predTokens.length;
  const recall = overlap / goldTokens.length;
  return (2 * precision * recall) / (precision + recall);
}

function rougeL(prediction, gold) {
  const pred = tokenize(prediction);
  const ref = tokenize(gold);
  if (!pred.length || !ref.length) return 0;
  const lcs = longestCommonSubsequence(pred, ref);
  return lcs / ref.length;
}

function pseudoJudge(prediction, gold) {
  return 0.5 * f1Score(prediction, gold) + 0.5 * rougeL(prediction, gold);
}

function faithfulness(prediction, memories) {
  if (!memories.length) return 0;
  const memoryText = memories.map((memory) => memory.summary).join(" ");
  return f1Score(prediction, memoryText);
}

function estimateCost({ history, query, memories, idlePredictionUsed, fallbackUsed, generatedAnswer }) {
  const inputTokens =
    estimateTokens(history.map((turn) => turn.text).join(" ")) +
    estimateTokens(query) +
    memories.reduce((sum, memory) => sum + estimateTokens(memory.summary), 0);
  const outputTokens = estimateTokens(generatedAnswer);

  return {
    input_tokens: inputTokens,
    output_tokens: outputTokens,
    total_tokens: inputTokens + outputTokens + (idlePredictionUsed ? 30 : 0),
    idle_time_cost: idlePredictionUsed ? 30 : 0,
    query_time_latency_ms: 6 + memories.length * 2 + (fallbackUsed ? 12 : 0)
  };
}

function average(rows, key) {
  if (!rows.length) return 0;
  return rows.reduce((sum, row) => sum + row[key], 0) / rows.length;
}

function counts(tokens) {
  const map = new Map();
  for (const token of tokens) {
    map.set(token, (map.get(token) || 0) + 1);
  }
  return map;
}

function longestCommonSubsequence(a, b) {
  const dp = Array.from({ length: a.length + 1 }, () => Array(b.length + 1).fill(0));
  for (let i = 1; i <= a.length; i += 1) {
    for (let j = 1; j <= b.length; j += 1) {
      dp[i][j] = a[i - 1] === b[j - 1] ? dp[i - 1][j - 1] + 1 : Math.max(dp[i - 1][j], dp[i][j - 1]);
    }
  }
  return dp[a.length][b.length];
}

module.exports = {
  average,
  computeActivationMetrics,
  estimateCost,
  f1Score,
  faithfulness,
  pseudoJudge,
  rougeL
};
