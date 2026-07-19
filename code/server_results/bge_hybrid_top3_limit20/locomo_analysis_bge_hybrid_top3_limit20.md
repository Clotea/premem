# LoCoMo Result Analysis

- Result file: `server_results\bge_hybrid_top3_limit20\locomo_time-sliced_qwen_vllm_bge_hybrid_top3_cuda2_limit20.json`
- Samples: 20

## Method Summary
| method | samples | precision | recall | hit_rate | fallback_rate | answer_f1 | rouge_l |
|---|---:|---:|---:|---:|---:|---:|---:|
| Random Cache | 20 | 0.0767 | 0.2750 | 0.3000 | 0.0000 | 0.0097 | 0.0417 |
| Recency Cache | 20 | 0.2167 | 0.8042 | 1.0000 | 0.0000 | 0.0178 | 0.0548 |
| Reactive Vector Retrieval | 20 | 0.1067 | 0.4500 | 0.5000 | 1.0000 | 0.0098 | 0.1214 |
| Reactive Graph Retrieval | 20 | 0.0667 | 0.2750 | 0.3000 | 1.0000 | 0.0084 | 0.1048 |
| LLM-Predict Cache Only | 20 | 0.3667 | 0.8208 | 1.0000 | 0.0000 | 0.0245 | 0.3702 |
| LLM-Predict + Fallback | 20 | 0.3667 | 0.8208 | 1.0000 | 0.0000 | 0.0245 | 0.3702 |
| Oracle Cache | 20 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0510 | 0.4440 |

## Main Comparisons

Compared with `Random Cache`:
- precision: 0.3667 vs 0.0767, delta=+0.2900, rel=+378.3%
- recall: 0.8208 vs 0.2750, delta=+0.5458, rel=+198.5%
- hit_rate: 1.0000 vs 0.3000, delta=+0.7000, rel=+233.3%
- f1: 0.0245 vs 0.0097, delta=+0.0148, rel=+151.8%
- rouge_l: 0.3702 vs 0.0417, delta=+0.3286, rel=+788.6%

Compared with `Recency Cache`:
- precision: 0.3667 vs 0.2167, delta=+0.1500, rel=+69.2%
- recall: 0.8208 vs 0.8042, delta=+0.0167, rel=+2.1%
- hit_rate: 1.0000 vs 1.0000, delta=+0.0000, rel=+0.0%
- f1: 0.0245 vs 0.0178, delta=+0.0067, rel=+37.4%
- rouge_l: 0.3702 vs 0.0548, delta=+0.3155, rel=+576.1%

Compared with `Reactive Vector Retrieval`:
- precision: 0.3667 vs 0.1067, delta=+0.2600, rel=+243.8%
- recall: 0.8208 vs 0.4500, delta=+0.3708, rel=+82.4%
- hit_rate: 1.0000 vs 0.5000, delta=+0.5000, rel=+100.0%
- f1: 0.0245 vs 0.0098, delta=+0.0147, rel=+150.6%
- rouge_l: 0.3702 vs 0.1214, delta=+0.2488, rel=+204.9%

Compared with `Reactive Graph Retrieval`:
- precision: 0.3667 vs 0.0667, delta=+0.3000, rel=+450.0%
- recall: 0.8208 vs 0.2750, delta=+0.5458, rel=+198.5%
- hit_rate: 1.0000 vs 0.3000, delta=+0.7000, rel=+233.3%
- f1: 0.0245 vs 0.0084, delta=+0.0161, rel=+192.5%
- rouge_l: 0.3702 vs 0.1048, delta=+0.2655, rel=+253.4%

Compared with `LLM-Predict Cache Only`:
- precision: 0.3667 vs 0.3667, delta=+0.0000, rel=+0.0%
- recall: 0.8208 vs 0.8208, delta=+0.0000, rel=+0.0%
- hit_rate: 1.0000 vs 1.0000, delta=+0.0000, rel=+0.0%
- f1: 0.0245 vs 0.0245, delta=+0.0000, rel=+0.0%
- rouge_l: 0.3702 vs 0.3702, delta=+0.0000, rel=+0.0%

## Verifier / Reranker Diagnosis
- Proactive precision: 0.1361
- Proactive recall: 0.8708
- Proactive hit_rate: 1.0000
- Prepared hit samples: 20/20 (100.0%)
- Prepared full-cover samples: 15/20 (75.0%)
- Final hit samples: 20/20 (100.0%)
- Final full-cover samples: 13/20 (65.0%)
- Selection losses: 0 samples where prepared hit but final missed
- Prepared misses: 0 samples where prepared missed all gold evidence
- Verifier providers: {'reranker:flagembedding': 20}
- Verifier decisions: {'use': 20}
- Reranker status: {'available': 20}

Representative selection losses:
- (none)

Representative prepared misses:
- (none)

Cache-only vs fallback:
- Cache-only recall=0.8208, fallback recall=0.8208
- Fallback rate=0.0000; if recall barely changes, fallback is not rescuing many misses.

## Literature-Level Check
- This run's answer F1 is 2.45 on a 0-100 scale.
- This run's evidence-selection recall is 82.08 on a 0-100 scale, with budget=5.
- Recency evidence recall in the same run is 80.42; the method is +1.67 points higher.
- Published LoCoMo papers usually report answer F1 / recall@k under their own retrieval units and k values, so the numbers are not strictly one-to-one comparable.
- The current run should not be claimed as surpassing paper-level LoCoMo results. It is below the original RAG-style LoCoMo answer-F1 range and also below recent dedicated memory-system reports.

## Bottom Line
- Current final precision=0.3667, recall=0.8208, hit_rate=1.0000.
- Compared with vector retrieval, recall delta=+0.3708.
- Compared with recency, recall delta=+0.0167.
- The method is competitive with the recency baseline on recall for this run, but answer F1 is still far below paper-level LoCoMo results.
