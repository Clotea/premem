function tokenize(text) {
  return String(text || "")
    .toLowerCase()
    .replace(/[^a-z0-9=\-]+/g, " ")
    .trim()
    .split(/\s+/)
    .filter(Boolean);
}

function unique(items) {
  return [...new Set(items)];
}

function jaccard(a, b) {
  const left = new Set(a);
  const right = new Set(b);
  if (left.size === 0 && right.size === 0) return 0;
  let intersection = 0;
  for (const item of left) {
    if (right.has(item)) intersection += 1;
  }
  return intersection / (left.size + right.size - intersection);
}

function overlapScore(query, memory) {
  const q = tokenize(query);
  const m = tokenize([
    memory.content,
    memory.summary,
    ...(memory.keywords || []),
    ...(memory.entities || [])
  ].join(" "));
  return jaccard(q, m);
}

function estimateTokens(text) {
  return Math.max(1, tokenize(text).length);
}

const STOPWORDS = new Set([
  "the",
  "and",
  "for",
  "that",
  "this",
  "with",
  "you",
  "your",
  "are",
  "was",
  "were",
  "have",
  "has",
  "had",
  "but",
  "not",
  "about",
  "from",
  "they",
  "them",
  "then",
  "there",
  "what",
  "when",
  "where",
  "why",
  "how",
  "who",
  "did",
  "does",
  "can",
  "could",
  "would",
  "should",
  "into",
  "onto",
  "too",
  "very",
  "really",
  "just",
  "like",
  "also"
]);

function extractKeywords(text, limit = 10) {
  const counts = new Map();
  for (const token of tokenize(text)) {
    if (token.length < 3 || STOPWORDS.has(token)) continue;
    counts.set(token, (counts.get(token) || 0) + 1);
  }
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, limit)
    .map(([token]) => token);
}

module.exports = {
  estimateTokens,
  extractKeywords,
  jaccard,
  overlapScore,
  tokenize,
  unique
};
