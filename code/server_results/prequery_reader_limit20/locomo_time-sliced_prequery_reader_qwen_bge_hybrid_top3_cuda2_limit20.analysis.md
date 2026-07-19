# LoCoMo Result Analysis

- Result file: `outputs/locomo_time-sliced_prequery_reader_qwen_bge_hybrid_top3_cuda2_limit20.json`
- Samples: 20

## Method Summary
| method | samples | precision | recall | hit_rate | fallback_rate | official_f1 | bleu1 | answer_f1 | rouge_l |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Random Cache | 20 | 0.0767 | 0.2750 | 0.3000 | 0.0000 | 0.0099 | 0.0057 | 0.0097 | 0.0417 |
| Recency Cache | 20 | 0.2167 | 0.8042 | 1.0000 | 0.0000 | 0.0173 | 0.0097 | 0.0178 | 0.0548 |
| Reactive Vector Retrieval | 20 | 0.1067 | 0.4500 | 0.5000 | 1.0000 | 0.0083 | 0.0042 | 0.0098 | 0.1214 |
| Reactive Graph Retrieval | 20 | 0.0667 | 0.2750 | 0.3000 | 1.0000 | 0.0076 | 0.0034 | 0.0084 | 0.1048 |
| LLM-Predict Cache Only | 20 | 0.3667 | 0.8208 | 1.0000 | 0.0000 | 0.0456 | 0.0124 | 0.0245 | 0.3702 |
| Pre-query Prepared + Reader | 20 | 0.1375 | 0.8708 | 1.0000 | 0.0000 | 0.2817 | 0.2660 | 0.2977 | 0.3161 |
| LLM-Predict + Fallback | 20 | 0.3667 | 0.8208 | 1.0000 | 0.0000 | 0.0456 | 0.0124 | 0.0245 | 0.3702 |
| Oracle Cache | 20 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0797 | 0.0293 | 0.0510 | 0.4440 |

## Main Comparisons

Compared with `Random Cache`:
- precision: 0.1375 vs 0.0767, delta=+0.0608, rel=+79.3%
- recall: 0.8708 vs 0.2750, delta=+0.5958, rel=+216.7%
- hit_rate: 1.0000 vs 0.3000, delta=+0.7000, rel=+233.3%
- official_f1: 0.2817 vs 0.0099, delta=+0.2718, rel=+2750.2%
- bleu1: 0.2660 vs 0.0057, delta=+0.2603, rel=+4530.5%
- f1: 0.2977 vs 0.0097, delta=+0.2880, rel=+2960.2%
- rouge_l: 0.3161 vs 0.0417, delta=+0.2744, rel=+658.6%

Compared with `Recency Cache`:
- precision: 0.1375 vs 0.2167, delta=-0.0792, rel=-36.6%
- recall: 0.8708 vs 0.8042, delta=+0.0667, rel=+8.3%
- hit_rate: 1.0000 vs 1.0000, delta=+0.0000, rel=+0.0%
- official_f1: 0.2817 vs 0.0173, delta=+0.2644, rel=+1530.9%
- bleu1: 0.2660 vs 0.0097, delta=+0.2563, rel=+2634.4%
- f1: 0.2977 vs 0.0178, delta=+0.2799, rel=+1570.0%
- rouge_l: 0.3161 vs 0.0548, delta=+0.2613, rel=+477.2%

Compared with `Reactive Vector Retrieval`:
- precision: 0.1375 vs 0.1067, delta=+0.0308, rel=+28.9%
- recall: 0.8708 vs 0.4500, delta=+0.4208, rel=+93.5%
- hit_rate: 1.0000 vs 0.5000, delta=+0.5000, rel=+100.0%
- official_f1: 0.2817 vs 0.0083, delta=+0.2734, rel=+3298.1%
- bleu1: 0.2660 vs 0.0042, delta=+0.2619, rel=+6307.7%
- f1: 0.2977 vs 0.0098, delta=+0.2879, rel=+2945.8%
- rouge_l: 0.3161 vs 0.1214, delta=+0.1946, rel=+160.3%

Compared with `Reactive Graph Retrieval`:
- precision: 0.1375 vs 0.0667, delta=+0.0708, rel=+106.2%
- recall: 0.8708 vs 0.2750, delta=+0.5958, rel=+216.7%
- hit_rate: 1.0000 vs 0.3000, delta=+0.7000, rel=+233.3%
- official_f1: 0.2817 vs 0.0076, delta=+0.2741, rel=+3619.7%
- bleu1: 0.2660 vs 0.0034, delta=+0.2627, rel=+7807.1%
- f1: 0.2977 vs 0.0084, delta=+0.2893, rel=+3455.3%
- rouge_l: 0.3161 vs 0.1048, delta=+0.2113, rel=+201.7%

Compared with `LLM-Predict Cache Only`:
- precision: 0.1375 vs 0.3667, delta=-0.2292, rel=-62.5%
- recall: 0.8708 vs 0.8208, delta=+0.0500, rel=+6.1%
- hit_rate: 1.0000 vs 1.0000, delta=+0.0000, rel=+0.0%
- official_f1: 0.2817 vs 0.0456, delta=+0.2360, rel=+517.4%
- bleu1: 0.2660 vs 0.0124, delta=+0.2536, rel=+2041.5%
- f1: 0.2977 vs 0.0245, delta=+0.2732, rel=+1114.5%
- rouge_l: 0.3161 vs 0.3702, delta=-0.0542, rel=-14.6%

## Verifier / Reranker Diagnosis
- Proactive precision: 0.1375
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
- This run's answer F1 is 28.17 on a 0-100 scale.
- This run's evidence-selection recall is 87.08 on a 0-100 scale, with budget=5.
- Recency evidence recall in the same run is 80.42; the method is +6.67 points higher.
- Published LoCoMo papers usually report answer F1 / recall@k under their own retrieval units and k values, so the numbers are not strictly one-to-one comparable.
- The current run should not be claimed as surpassing paper-level LoCoMo results. It is below the original RAG-style LoCoMo answer-F1 range and also below recent dedicated memory-system reports.

## Bottom Line
- Current final precision=0.1375, recall=0.8708, hit_rate=1.0000.
- Compared with vector retrieval, recall delta=+0.4208.
- Compared with recency, recall delta=+0.0667.
- The method is competitive with the recency baseline on recall for this run, but answer F1 is still far below paper-level LoCoMo results.
