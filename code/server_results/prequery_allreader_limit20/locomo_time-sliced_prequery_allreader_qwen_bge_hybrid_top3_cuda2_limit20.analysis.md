# LoCoMo Result Analysis

- Result file: `outputs/locomo_time-sliced_prequery_allreader_qwen_bge_hybrid_top3_cuda2_limit20.json`
- Samples: 20

## Method Summary
| method | samples | selected | precision | recall | hit_rate | fallback_rate | official_f1 | bleu1 | answer_f1 | rouge_l |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Random Cache | 20 | 0.40 | 0.0767 | 0.2750 | 0.3000 | 0.0000 | 0.0200 | 0.0167 | 0.0200 | 0.0250 |
| Recency Cache | 20 | 0.60 | 0.2167 | 0.8042 | 1.0000 | 0.0000 | 0.0343 | 0.0222 | 0.0325 | 0.0333 |
| Reactive Vector Retrieval | 20 | 4.90 | 0.1067 | 0.4500 | 0.5000 | 1.0000 | 0.0454 | 0.0306 | 0.0436 | 0.0500 |
| Reactive Graph Retrieval | 20 | 4.90 | 0.0667 | 0.2750 | 0.3000 | 1.0000 | 0.0343 | 0.0222 | 0.0325 | 0.0333 |
| LLM-Predict Cache Only | 20 | 3.00 | 0.3667 | 0.8208 | 1.0000 | 0.0000 | 0.2302 | 0.1993 | 0.2401 | 0.2714 |
| Pre-query Prepared + Reader | 20 | 9.80 | 0.1379 | 0.8708 | 1.0000 | 0.0000 | 0.2821 | 0.2672 | 0.2985 | 0.3161 |
| LLM-Predict + Fallback | 20 | 3.00 | 0.3667 | 0.8208 | 1.0000 | 0.0000 | 0.2302 | 0.1993 | 0.2401 | 0.2714 |
| Oracle Cache | 20 | 1.55 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.3946 | 0.3400 | 0.3905 | 0.3786 |

## Main Comparisons

Compared with `Random Cache`:
- precision: 0.1379 vs 0.0767, delta=+0.0613, rel=+79.9%
- recall: 0.8708 vs 0.2750, delta=+0.5958, rel=+216.7%
- hit_rate: 1.0000 vs 0.3000, delta=+0.7000, rel=+233.3%
- official_f1: 0.2821 vs 0.0200, delta=+0.2621, rel=+1310.6%
- bleu1: 0.2672 vs 0.0167, delta=+0.2505, rel=+1502.9%
- f1: 0.2985 vs 0.0200, delta=+0.2785, rel=+1392.7%
- rouge_l: 0.3161 vs 0.0250, delta=+0.2911, rel=+1164.3%

Compared with `Recency Cache`:
- precision: 0.1379 vs 0.2167, delta=-0.0787, rel=-36.3%
- recall: 0.8708 vs 0.8042, delta=+0.0667, rel=+8.3%
- hit_rate: 1.0000 vs 1.0000, delta=+0.0000, rel=+0.0%
- official_f1: 0.2821 vs 0.0343, delta=+0.2478, rel=+722.8%
- bleu1: 0.2672 vs 0.0222, delta=+0.2449, rel=+1100.9%
- f1: 0.2985 vs 0.0325, delta=+0.2660, rel=+818.6%
- rouge_l: 0.3161 vs 0.0333, delta=+0.2827, rel=+848.2%

Compared with `Reactive Vector Retrieval`:
- precision: 0.1379 vs 0.1067, delta=+0.0313, rel=+29.3%
- recall: 0.8708 vs 0.4500, delta=+0.4208, rel=+93.5%
- hit_rate: 1.0000 vs 0.5000, delta=+0.5000, rel=+100.0%
- official_f1: 0.2821 vs 0.0454, delta=+0.2367, rel=+521.4%
- bleu1: 0.2672 vs 0.0306, delta=+0.2366, rel=+773.7%
- f1: 0.2985 vs 0.0436, delta=+0.2549, rel=+584.6%
- rouge_l: 0.3161 vs 0.0500, delta=+0.2661, rel=+532.1%

Compared with `Reactive Graph Retrieval`:
- precision: 0.1379 vs 0.0667, delta=+0.0713, rel=+106.9%
- recall: 0.8708 vs 0.2750, delta=+0.5958, rel=+216.7%
- hit_rate: 1.0000 vs 0.3000, delta=+0.7000, rel=+233.3%
- official_f1: 0.2821 vs 0.0343, delta=+0.2478, rel=+722.8%
- bleu1: 0.2672 vs 0.0222, delta=+0.2449, rel=+1100.9%
- f1: 0.2985 vs 0.0325, delta=+0.2660, rel=+818.6%
- rouge_l: 0.3161 vs 0.0333, delta=+0.2827, rel=+848.2%

Compared with `LLM-Predict Cache Only`:
- precision: 0.1379 vs 0.3667, delta=-0.2287, rel=-62.4%
- recall: 0.8708 vs 0.8208, delta=+0.0500, rel=+6.1%
- hit_rate: 1.0000 vs 1.0000, delta=+0.0000, rel=+0.0%
- official_f1: 0.2821 vs 0.2302, delta=+0.0519, rel=+22.6%
- bleu1: 0.2672 vs 0.1993, delta=+0.0678, rel=+34.0%
- f1: 0.2985 vs 0.2401, delta=+0.0585, rel=+24.4%
- rouge_l: 0.3161 vs 0.2714, delta=+0.0446, rel=+16.4%

## Verifier / Reranker Diagnosis
- Proactive precision: 0.1379
- Proactive recall: 0.8708
- Proactive hit_rate: 1.0000
- Prepared hit samples: 20/20 (100.0%)
- Prepared full-cover samples: 15/20 (75.0%)
- Final hit samples: 20/20 (100.0%)
- Final full-cover samples: 15/20 (75.0%)
- Selection losses: 0 samples where prepared hit but final missed
- Prepared misses: 0 samples where prepared missed all gold evidence
- Verifier providers: {'reranker:flagembedding': 20}
- Verifier decisions: {'use': 20}
- Reranker status: {'available': 20}

Representative selection losses:
- (none)

Representative prepared misses:
- (none)

Cache-only vs pre-query reader:
- Cache-only recall=0.8208, pre-query prepared recall=0.8708
- Fallback rate=0.0000; this target method does not use post-query fallback for memory selection.

## Literature-Level Check
- This run's answer F1 is 28.21 on a 0-100 scale.
- This run's evidence-selection recall is 87.08 on a 0-100 scale; cache budget=5, avg selected memories=9.80.
- Recency evidence recall in the same run is 80.42; the method is +6.67 points higher.
- Published LoCoMo papers usually report answer F1 / recall@k under their own retrieval units and k values, so the numbers are not strictly one-to-one comparable.
- The current run should not be claimed as surpassing paper-level LoCoMo results. It is below the original RAG-style LoCoMo answer-F1 range and also below recent dedicated memory-system reports.

## Bottom Line
- Current final precision=0.1379, recall=0.8708, hit_rate=1.0000.
- Compared with vector retrieval, recall delta=+0.4208.
- Compared with recency, recall delta=+0.0667.
- The method is competitive with the recency baseline on recall for this run, but answer F1 is still far below paper-level LoCoMo results.
