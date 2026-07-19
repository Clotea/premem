# LoCoMo Result Analysis

- Result file: `server_results\bge_hybrid_top5_limit20\locomo_time-sliced_qwen_vllm_bge_hybrid_top5_cuda2_limit20.json`
- Samples: 20

## Method Summary
| method | samples | precision | recall | hit_rate | fallback_rate | answer_f1 | rouge_l |
|---|---:|---:|---:|---:|---:|---:|---:|
| Random Cache | 20 | 0.0767 | 0.2750 | 0.3000 | 0.0000 | 0.0097 | 0.0417 |
| Recency Cache | 20 | 0.2167 | 0.8042 | 1.0000 | 0.0000 | 0.0178 | 0.0548 |
| Reactive Vector Retrieval | 20 | 0.1067 | 0.4500 | 0.5000 | 1.0000 | 0.0098 | 0.1214 |
| Reactive Graph Retrieval | 20 | 0.0667 | 0.2750 | 0.3000 | 1.0000 | 0.0084 | 0.1048 |
| LLM-Predict Cache Only | 20 | 0.1467 | 0.5333 | 0.6000 | 0.0000 | 0.0131 | 0.2232 |
| LLM-Predict + Fallback | 20 | 0.1467 | 0.5333 | 0.6000 | 0.0000 | 0.0131 | 0.2232 |
| Oracle Cache | 20 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0510 | 0.4440 |

## Main Comparisons

Compared with `Random Cache`:
- precision: 0.1467 vs 0.0767, delta=+0.0700, rel=+91.3%
- recall: 0.5333 vs 0.2750, delta=+0.2583, rel=+93.9%
- hit_rate: 0.6000 vs 0.3000, delta=+0.3000, rel=+100.0%
- f1: 0.0131 vs 0.0097, delta=+0.0034, rel=+34.7%
- rouge_l: 0.2232 vs 0.0417, delta=+0.1815, rel=+435.7%

Compared with `Recency Cache`:
- precision: 0.1467 vs 0.2167, delta=-0.0700, rel=-32.3%
- recall: 0.5333 vs 0.8042, delta=-0.2708, rel=-33.7%
- hit_rate: 0.6000 vs 1.0000, delta=-0.4000, rel=-40.0%
- f1: 0.0131 vs 0.0178, delta=-0.0047, rel=-26.5%
- rouge_l: 0.2232 vs 0.0548, delta=+0.1685, rel=+307.6%

Compared with `Reactive Vector Retrieval`:
- precision: 0.1467 vs 0.1067, delta=+0.0400, rel=+37.5%
- recall: 0.5333 vs 0.4500, delta=+0.0833, rel=+18.5%
- hit_rate: 0.6000 vs 0.5000, delta=+0.1000, rel=+20.0%
- f1: 0.0131 vs 0.0098, delta=+0.0033, rel=+34.1%
- rouge_l: 0.2232 vs 0.1214, delta=+0.1018, rel=+83.8%

Compared with `Reactive Graph Retrieval`:
- precision: 0.1467 vs 0.0667, delta=+0.0800, rel=+120.0%
- recall: 0.5333 vs 0.2750, delta=+0.2583, rel=+93.9%
- hit_rate: 0.6000 vs 0.3000, delta=+0.3000, rel=+100.0%
- f1: 0.0131 vs 0.0084, delta=+0.0047, rel=+56.5%
- rouge_l: 0.2232 vs 0.1048, delta=+0.1185, rel=+113.1%

Compared with `LLM-Predict Cache Only`:
- precision: 0.1467 vs 0.1467, delta=+0.0000, rel=+0.0%
- recall: 0.5333 vs 0.5333, delta=+0.0000, rel=+0.0%
- hit_rate: 0.6000 vs 0.6000, delta=+0.0000, rel=+0.0%
- f1: 0.0131 vs 0.0131, delta=+0.0000, rel=+0.0%
- rouge_l: 0.2232 vs 0.2232, delta=+0.0000, rel=+0.0%

## Verifier / Reranker Diagnosis
- Proactive precision: 0.1350
- Proactive recall: 0.8458
- Proactive hit_rate: 1.0000
- Prepared hit samples: 20/20 (100.0%)
- Prepared full-cover samples: 14/20 (70.0%)
- Final hit samples: 12/20 (60.0%)
- Final full-cover samples: 9/20 (45.0%)
- Selection losses: 8 samples where prepared hit but final missed
- Prepared misses: 0 samples where prepared missed all gold evidence
- Verifier providers: {'reranker:flagembedding': 20}
- Verifier decisions: {'use': 20}
- Reranker status: {'available': 20}

Representative selection losses:
- locomo_c01_tsqa_008: gold=m_032,m_048; prepared=m_048,m_047,m_046,m_028,m_038,m_025,m_035,m_027,m_032,m_040,m_007,m_003; verifier=m_028,m_035,m_047,m_025,m_027; final=m_028,m_035,m_047,m_025,m_027; provider=reranker:flagembedding
- locomo_c01_tsqa_010: gold=m_046; prepared=m_046,m_045,m_028,m_038,m_042,m_025,m_026,m_035,m_027,m_005,m_009,m_003; verifier=m_028,m_026,m_027,m_035,m_025; final=m_028,m_026,m_027,m_035,m_025; provider=reranker:flagembedding
- locomo_c01_tsqa_011: gold=m_048; prepared=m_048,m_047,m_046,m_028,m_038,m_025,m_035,m_027,m_032,m_040,m_007,m_003; verifier=m_028,m_047,m_035,m_025,m_027; final=m_028,m_047,m_035,m_025,m_027; provider=reranker:flagembedding
- locomo_c01_tsqa_012: gold=m_048,m_061; prepared=m_061,m_058,m_039,m_028,m_060,m_026,m_025,m_035; verifier=m_058,m_028,m_026,m_035,m_025; final=m_058,m_028,m_026,m_035,m_025; provider=reranker:flagembedding
- locomo_c01_tsqa_013: gold=m_063; prepared=m_062,m_063,m_028,m_061,m_036,m_025,m_035,m_027,m_032,m_004,m_041,m_002; verifier=m_028,m_036,m_035,m_025,m_027; final=m_028,m_036,m_035,m_025,m_027; provider=reranker:flagembedding

Representative prepared misses:
- (none)

Cache-only vs fallback:
- Cache-only recall=0.5333, fallback recall=0.5333
- Fallback rate=0.0000; if recall barely changes, fallback is not rescuing many misses.

## Literature-Level Check
- This run's answer F1 is 1.31 on a 0-100 scale.
- This run's evidence-selection recall is 53.33 on a 0-100 scale, with budget=3.
- Recency evidence recall in the same run is 80.42, much higher than the method result.
- Published LoCoMo papers usually report answer F1 / recall@k under their own retrieval units and k values, so the numbers are not strictly one-to-one comparable.
- The current run should not be claimed as surpassing paper-level LoCoMo results. It is below the original RAG-style LoCoMo answer-F1 range and also below recent dedicated memory-system reports.

## Bottom Line
- Current final precision=0.1467, recall=0.5333, hit_rate=0.6000.
- Compared with vector retrieval, recall delta=+0.0833.
- Compared with recency, recall delta=-0.2708.
- The method is not yet competitive with the strong recency baseline on this time-sliced setup.
