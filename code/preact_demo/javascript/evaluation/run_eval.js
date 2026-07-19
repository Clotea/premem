const { buildHeterogeneousGraph } = require("../graph/build_graph");
const { memoryWriter } = require("../memory/memory_writer");
const { llmPredictActivation } = require("../activation/llm_predictor");
const { buildWorkingCache } = require("../activation/working_cache");
const { verifyCache } = require("../activation/verifier");
const { vectorRetrieve } = require("../retrieval/vector_retriever");
const { graphRetrieve } = require("../retrieval/graph_retriever");
const { generateAnswer } = require("../generation/answer_generator");
const { labelGoldEvidence } = require("./evidence_labeler");
const {
  average,
  computeActivationMetrics,
  estimateCost,
  f1Score,
  faithfulness,
  pseudoJudge,
  rougeL
} = require("./metrics");

function runEvaluation(samples, config) {
  const methods = [
    "Random Cache",
    "Recency Cache",
    "Reactive Vector Retrieval",
    "Reactive Graph Retrieval",
    "LLM-Predict Cache Only",
    "LLM-Predict + Fallback",
    "Oracle Cache"
  ];
  const results = Object.fromEntries(methods.map((method) => [method, []]));
  const contextCache = new Map();

  for (const sample of samples) {
    const contextKey = sample.history_cache_key || sample.id;
    let context = contextCache.get(contextKey);
    if (!context) {
      const memoryNodes = memoryWriter(sample.history);
      const graph = buildHeterogeneousGraph(memoryNodes, sample.history, config);
      const prediction = llmPredictActivation({
        history: sample.history,
        memoryNodes,
        budget: config.cacheBudget
      });
      const cache = buildWorkingCache({
        cacheId: `cache_${contextKey}`,
        budget: config.cacheBudget,
        prediction,
        memoryNodes,
        graph
      });
      context = { cache, graph, memoryNodes };
      contextCache.set(contextKey, context);
    }

    const { cache, graph, memoryNodes } = context;
    const evidence = labelGoldEvidence({
      evidenceTerms: sample.evidence_terms,
      evidenceTurnIds: sample.gold_evidence_turn_ids,
      memoryNodes
    });

    evaluateCacheMethod({
      method: "Random Cache",
      sample,
      memoryNodes,
      cacheIds: seededPick(memoryNodes.map((memory) => memory.id), config.cacheBudget, config.randomSeed),
      evidence,
      config,
      results
    });

    evaluateCacheMethod({
      method: "Recency Cache",
      sample,
      memoryNodes,
      cacheIds: [...memoryNodes]
        .sort((a, b) => b.timestamp - a.timestamp)
        .slice(0, config.cacheBudget)
        .map((memory) => memory.id),
      evidence,
      config,
      results
    });

    evaluateRetrievalMethod({
      method: "Reactive Vector Retrieval",
      sample,
      memoryNodes,
      selected: vectorRetrieve(sample.question, memoryNodes, config.retrievalTopK),
      evidence,
      fallbackUsed: true,
      idlePredictionUsed: false,
      results
    });

    evaluateRetrievalMethod({
      method: "Reactive Graph Retrieval",
      sample,
      memoryNodes,
      selected: graphRetrieve(sample.question, graph, memoryNodes, config.retrievalTopK),
      evidence,
      fallbackUsed: true,
      idlePredictionUsed: false,
      results
    });

    const cacheOnlyVerified = verifyCache({
      query: sample.question,
      cache,
      memoryNodes,
      threshold: config.verifierThreshold
    });
    evaluateRetrievalMethod({
      method: "LLM-Predict Cache Only",
      sample,
      memoryNodes,
      selected: cacheOnlyVerified.memories,
      activated: cache.memory_ids,
      evidence,
      fallbackUsed: false,
      idlePredictionUsed: true,
      results
    });

    const fallbackSelected = cacheOnlyVerified.sufficient
      ? cacheOnlyVerified.memories
      : fallbackRetrieve(sample.question, graph, memoryNodes, config);
    evaluateRetrievalMethod({
      method: "LLM-Predict + Fallback",
      sample,
      memoryNodes,
      selected: fallbackSelected,
      activated: cache.memory_ids,
      evidence,
      fallbackUsed: !cacheOnlyVerified.sufficient,
      idlePredictionUsed: true,
      results
    });

    evaluateRetrievalMethod({
      method: "Oracle Cache",
      sample,
      memoryNodes,
      selected: memoryNodes.filter((memory) => evidence.includes(memory.id)).slice(0, config.cacheBudget),
      activated: evidence.slice(0, config.cacheBudget),
      evidence,
      fallbackUsed: false,
      idlePredictionUsed: false,
      results
    });
  }

  return summarize(results, config.cacheBudget);
}

function fallbackRetrieve(query, graph, memoryNodes, config) {
  if (config.fallbackRetriever === "graph") {
    return graphRetrieve(query, graph, memoryNodes, config.retrievalTopK);
  }
  return vectorRetrieve(query, memoryNodes, config.retrievalTopK);
}

function evaluateCacheMethod({ method, sample, memoryNodes, cacheIds, evidence, config, results }) {
  const memoryById = new Map(memoryNodes.map((memory) => [memory.id, memory]));
  const cache = {
    memory_ids: cacheIds,
    summaries: cacheIds.map((id) => memoryById.get(id).summary)
  };
  const verified = verifyCache({
    query: sample.question,
    cache,
    memoryNodes,
    threshold: config.verifierThreshold
  });

  evaluateRetrievalMethod({
    method,
    sample,
    memoryNodes,
    selected: verified.memories,
    activated: cacheIds,
    evidence,
    fallbackUsed: false,
    idlePredictionUsed: method !== "Random Cache" && method !== "Recency Cache",
    results
  });
}

function evaluateRetrievalMethod({
  method,
  sample,
  selected,
  activated,
  evidence,
  fallbackUsed,
  idlePredictionUsed,
  results
}) {
  const answer = generateAnswer(sample.question, selected);
  const activation = computeActivationMetrics(activated || selected.map((memory) => memory.id), evidence);
  const costs = estimateCost({
    history: sample.history,
    query: sample.question,
    memories: selected,
    idlePredictionUsed,
    fallbackUsed,
    generatedAnswer: answer
  });

  results[method].push({
    sample_id: sample.id,
    precision: activation.precision,
    recall: activation.recall,
    hit_rate: activation.hit_rate,
    wasted_rate: activation.wasted_rate,
    fallback_rate: fallbackUsed ? 1 : 0,
    f1: f1Score(answer, sample.answer),
    rouge_l: rougeL(answer, sample.answer),
    llm_judge: pseudoJudge(answer, sample.answer),
    faithfulness: faithfulness(answer, selected),
    query_time_latency_ms: costs.query_time_latency_ms,
    idle_time_cost: costs.idle_time_cost,
    total_tokens: costs.total_tokens,
    selected_memory_ids: selected.map((memory) => memory.id)
  });
}

function summarize(results, budget) {
  return Object.entries(results).map(([method, rows]) => ({
    method,
    budget,
    precision: average(rows, "precision"),
    recall: average(rows, "recall"),
    hit_rate: average(rows, "hit_rate"),
    wasted_rate: average(rows, "wasted_rate"),
    fallback_rate: average(rows, "fallback_rate"),
    f1: average(rows, "f1"),
    rouge_l: average(rows, "rouge_l"),
    llm_judge: average(rows, "llm_judge"),
    faithfulness: average(rows, "faithfulness"),
    query_time_latency_ms: average(rows, "query_time_latency_ms"),
    idle_time_cost: average(rows, "idle_time_cost"),
    total_tokens: average(rows, "total_tokens"),
    samples: rows
  }));
}

function seededPick(items, count, seed) {
  return [...items]
    .map((item, index) => ({ item, key: Math.sin(seed + index * 997) }))
    .sort((a, b) => a.key - b.key)
    .slice(0, count)
    .map((entry) => entry.item);
}

module.exports = {
  runEvaluation
};
