# 2026-07-20: PreMem vs LongMemEval-style Full-History Prompting

## Baseline reproduced

The comparison baseline adapts LongMemEval's documented
`full-history-session / JSON / useronly=false / con` setup to LoCoMo:

- all timestamped history sessions and both speakers are passed at query time;
- image query and caption metadata are retained;
- the prompt asks the model to identify relevant facts and reason internally;
- no retrieval, pre-query prediction, metapath, reranker, or cache is used;
- the reader is the same Qwen2.5-7B vLLM endpoint as PreMem.

This is an adaptation, not a claim that these are LongMemEval paper numbers.

## Head-10 result

| Metric | Pre-query Prepared | Full-History Prompting | MemoryNode Oracle |
|---|---:|---:|---:|
| Official F1 | 0.5822 | 0.4271 | 0.7022 |
| Temporal F1 | 0.8000 | 0.3643 | 0.8000 |
| Mem0-style judge | 0.60 | 0.40 | 0.70 |
| Strict judge | 0.50 | 0.30 | 0.60 |
| Evidence recall | 0.95 | 1.00 | 1.00 |
| Full cover | 0.90 | 1.00 | 1.00 |
| Context turns/memories | 4.8 | 23.1 | 1.2 |
| Reader prompt tokens | 635.7 | 1050.3 | 282.2 |
| Reader API E2E | 278.3 ms | 257.6 ms | 196.2 ms |

PreMem uses 39.5% fewer reader prompt tokens and 79.2% fewer context items
than full history while gaining 0.1551 absolute official F1. Full history has
perfect evidence inclusion but lower answer quality, consistent with temporal
reasoning failures and context distraction on this slice.

No reader-latency win is established: the full-history call is about 21 ms
faster on average despite its larger prompt. This is a ten-sample,
non-streaming API-E2E measurement affected by output length and request noise,
not a p95 TTFT result.

## Important threats to validity

1. The time-sliced cutoff is chosen using the latest gold-evidence turn. It
   hides the question from planning, but still uses annotation knowledge to
   construct the prefix and may inflate recency/prepared recall.
2. Six of ten questions are temporal and all come from conversation 1.
3. PreMem receives deterministic resolved-time annotations; this baseline gets
   timestamps and an instruction to resolve dates. A same-preprocessing
   ablation is needed to isolate cache selection from temporal normalization.
4. Average full history here is only 23 turns / 1050 prompt tokens, not a
   genuinely long-context stress test.
5. Planning still averages roughly 16.8 seconds. The quality/token advantage
   does not prove that preparation completes before a real query.
6. Judge calls reuse Qwen2.5-7B and are diagnostic rather than independent
   leaderboard-grade evaluation.
