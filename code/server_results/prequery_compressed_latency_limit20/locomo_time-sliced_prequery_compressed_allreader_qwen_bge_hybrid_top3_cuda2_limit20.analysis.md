# LoCoMo Result Analysis

- Result file: `outputs/locomo_time-sliced_prequery_compressed_allreader_qwen_bge_hybrid_top3_cuda2_limit20.json`
- Samples: 20

## Method Summary
| method | samples | selected | precision | recall | hit_rate | full_cover | query_retrieval_ms | official_f1 | bleu1 | rouge_l |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Random Cache | 20 | 0.40 | 0.0767 | 0.2750 | 0.3000 | 0.2500 | 0.525 | 0.0200 | 0.0167 | 0.0250 |
| Recency Cache | 20 | 0.60 | 0.2167 | 0.8042 | 1.0000 | 0.6500 | 0.461 | 0.0343 | 0.0222 | 0.0333 |
| Reactive Vector Retrieval | 20 | 4.90 | 0.1067 | 0.4500 | 0.5000 | 0.4000 | 3.893 | 0.0454 | 0.0306 | 0.0500 |
| Reactive Graph Retrieval | 20 | 4.90 | 0.0667 | 0.2750 | 0.3000 | 0.2500 | 5.604 | 0.0343 | 0.0222 | 0.0333 |
| LLM-Predict Cache Only | 20 | 3.00 | 0.3667 | 0.8208 | 1.0000 | 0.6500 | 56.514 | 0.2295 | 0.1981 | 0.2589 |
| Pre-query Prepared + Reader | 20 | 4.90 | 0.2267 | 0.8208 | 1.0000 | 0.6500 | 0.025 | 0.2384 | 0.1981 | 0.2589 |
| LLM-Predict + Fallback | 20 | 3.00 | 0.3667 | 0.8208 | 1.0000 | 0.6500 | 56.514 | 0.2295 | 0.1981 | 0.2589 |
| Oracle Cache | 20 | 1.55 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.048 | 0.3946 | 0.3400 | 0.3786 |

## Main Comparisons

Compared with `Random Cache`:
- selected_count: 4.9000 vs 0.4000, delta=+4.5000, rel=+1125.0%
- precision: 0.2267 vs 0.0767, delta=+0.1500, rel=+195.7%
- recall: 0.8208 vs 0.2750, delta=+0.5458, rel=+198.5%
- hit_rate: 1.0000 vs 0.3000, delta=+0.7000, rel=+233.3%
- full_cover_rate: 0.6500 vs 0.2500, delta=+0.4000, rel=+160.0%
- query_retrieval_latency_ms: 0.0245 vs 0.5246, delta=-0.5001, rel=-95.3%
- official_f1: 0.2384 vs 0.0200, delta=+0.2184, rel=+1091.8%
- bleu1: 0.1981 vs 0.0167, delta=+0.1815, rel=+1088.9%
- f1: 0.2373 vs 0.0200, delta=+0.2173, rel=+1086.4%
- rouge_l: 0.2589 vs 0.0250, delta=+0.2339, rel=+935.7%

Compared with `Recency Cache`:
- selected_count: 4.9000 vs 0.6000, delta=+4.3000, rel=+716.7%
- precision: 0.2267 vs 0.2167, delta=+0.0100, rel=+4.6%
- recall: 0.8208 vs 0.8042, delta=+0.0167, rel=+2.1%
- hit_rate: 1.0000 vs 1.0000, delta=+0.0000, rel=+0.0%
- full_cover_rate: 0.6500 vs 0.6500, delta=+0.0000, rel=+0.0%
- query_retrieval_latency_ms: 0.0245 vs 0.4611, delta=-0.4366, rel=-94.7%
- official_f1: 0.2384 vs 0.0343, delta=+0.2041, rel=+595.2%
- bleu1: 0.1981 vs 0.0222, delta=+0.1759, rel=+790.7%
- f1: 0.2373 vs 0.0325, delta=+0.2048, rel=+630.1%
- rouge_l: 0.2589 vs 0.0333, delta=+0.2256, rel=+676.8%

Compared with `Reactive Vector Retrieval`:
- selected_count: 4.9000 vs 4.9000, delta=+0.0000, rel=+0.0%
- precision: 0.2267 vs 0.1067, delta=+0.1200, rel=+112.5%
- recall: 0.8208 vs 0.4500, delta=+0.3708, rel=+82.4%
- hit_rate: 1.0000 vs 0.5000, delta=+0.5000, rel=+100.0%
- full_cover_rate: 0.6500 vs 0.4000, delta=+0.2500, rel=+62.5%
- query_retrieval_latency_ms: 0.0245 vs 3.8931, delta=-3.8686, rel=-99.4%
- official_f1: 0.2384 vs 0.0454, delta=+0.1930, rel=+425.1%
- bleu1: 0.1981 vs 0.0306, delta=+0.1676, rel=+548.0%
- f1: 0.2373 vs 0.0436, delta=+0.1937, rel=+444.1%
- rouge_l: 0.2589 vs 0.0500, delta=+0.2089, rel=+417.9%

Compared with `Reactive Graph Retrieval`:
- selected_count: 4.9000 vs 4.9000, delta=+0.0000, rel=+0.0%
- precision: 0.2267 vs 0.0667, delta=+0.1600, rel=+240.0%
- recall: 0.8208 vs 0.2750, delta=+0.5458, rel=+198.5%
- hit_rate: 1.0000 vs 0.3000, delta=+0.7000, rel=+233.3%
- full_cover_rate: 0.6500 vs 0.2500, delta=+0.4000, rel=+160.0%
- query_retrieval_latency_ms: 0.0245 vs 5.6038, delta=-5.5793, rel=-99.6%
- official_f1: 0.2384 vs 0.0343, delta=+0.2041, rel=+595.2%
- bleu1: 0.1981 vs 0.0222, delta=+0.1759, rel=+790.7%
- f1: 0.2373 vs 0.0325, delta=+0.2048, rel=+630.1%
- rouge_l: 0.2589 vs 0.0333, delta=+0.2256, rel=+676.8%

Compared with `LLM-Predict Cache Only`:
- selected_count: 4.9000 vs 3.0000, delta=+1.9000, rel=+63.3%
- precision: 0.2267 vs 0.3667, delta=-0.1400, rel=-38.2%
- recall: 0.8208 vs 0.8208, delta=+0.0000, rel=+0.0%
- hit_rate: 1.0000 vs 1.0000, delta=+0.0000, rel=+0.0%
- full_cover_rate: 0.6500 vs 0.6500, delta=+0.0000, rel=+0.0%
- query_retrieval_latency_ms: 0.0245 vs 56.5140, delta=-56.4894, rel=-100.0%
- official_f1: 0.2384 vs 0.2295, delta=+0.0089, rel=+3.9%
- bleu1: 0.1981 vs 0.1981, delta=+0.0000, rel=+0.0%
- f1: 0.2373 vs 0.2373, delta=+0.0000, rel=+0.0%
- rouge_l: 0.2589 vs 0.2589, delta=+0.0000, rel=+0.0%

## Previous-Run Comparison

Compared with `previous Pre-query Prepared + Reader`:
- selected_count: 4.9000 vs 9.8000, delta=-4.9000, rel=-50.0%
- precision: 0.2267 vs 0.1379, delta=+0.0887, rel=+64.3%
- recall: 0.8208 vs 0.8708, delta=-0.0500, rel=-5.7%
- hit_rate: 1.0000 vs 1.0000, delta=+0.0000, rel=+0.0%
- full_cover_rate: 0.6500 vs 0.0000, delta=+0.6500, rel=+0.0%
- query_retrieval_latency_ms: 0.0245 vs 0.0000, delta=+0.0245, rel=+0.0%
- official_f1: 0.2384 vs 0.2821, delta=-0.0438, rel=-15.5%
- bleu1: 0.1981 vs 0.2672, delta=-0.0690, rel=-25.8%
- f1: 0.2373 vs 0.2985, delta=-0.0613, rel=-20.5%
- rouge_l: 0.2589 vs 0.3161, delta=-0.0571, rel=-18.1%

## Verifier / Reranker Diagnosis
- Proactive precision: 0.2267
- Proactive recall: 0.8208
- Proactive hit_rate: 1.0000
- Proactive full_cover_rate: 0.6500
- Avg query-time retrieval latency: 0.025 ms
- Prepared hit samples: 20/20 (100.0%)
- Prepared full-cover samples: 13/20 (65.0%)
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

Cache-only vs pre-query reader:
- Cache-only recall=0.8208, pre-query prepared recall=0.8208
- Cache-only query retrieval=56.514 ms, pre-query cache read=0.025 ms
- Fallback rate=0.0000; this target method does not use post-query fallback for memory selection.

## Literature-Level Check
- This run's answer F1 is 23.84 on a 0-100 scale.
- This run's evidence-selection recall is 82.08 and full-cover is 65.00 on a 0-100 scale; cache budget=5, avg selected memories=4.90.
- Avg measured query-time retrieval latency is 0.025 ms for the target method.
- Recency evidence recall in the same run is 80.42; the method is +1.67 points higher.
- Published LoCoMo papers usually report answer F1 / recall@k under their own retrieval units and k values, so the numbers are not strictly one-to-one comparable.
- The current run should not be claimed as surpassing paper-level LoCoMo results. It is below the original RAG-style LoCoMo answer-F1 range and also below recent dedicated memory-system reports.

## Bottom Line
- Current final precision=0.2267, recall=0.8208, hit_rate=1.0000, full_cover=0.6500.
- Query-time retrieval latency=0.025 ms; vector retrieval latency=3.893 ms.
- Compared with vector retrieval, recall delta=+0.3708.
- Compared with recency, recall delta=+0.0167.
- The method is competitive with the recency baseline on recall for this run, but answer F1 is still far below paper-level LoCoMo results.
