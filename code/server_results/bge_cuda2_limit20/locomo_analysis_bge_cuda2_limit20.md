# LoCoMo Result Analysis

- Result file: `server_results\bge_cuda2_limit20\locomo_time-sliced_qwen_vllm_bge_cuda2_limit20.json`
- Samples: 20

## Method Summary
| method | samples | precision | recall | hit_rate | fallback_rate | answer_f1 | rouge_l |
|---|---:|---:|---:|---:|---:|---:|---:|
| Random Cache | 20 | 0.0667 | 0.1750 | 0.2000 | 0.0000 | 0.0145 | 0.0488 |
| Recency Cache | 20 | 0.3500 | 0.8042 | 1.0000 | 0.0000 | 0.0257 | 0.0798 |
| Reactive Vector Retrieval | 20 | 0.1000 | 0.2750 | 0.3000 | 1.0000 | 0.0106 | 0.0631 |
| Reactive Graph Retrieval | 20 | 0.0500 | 0.1500 | 0.1500 | 1.0000 | 0.0038 | 0.0250 |
| LLM-Predict Cache Only | 20 | 0.5000 | 0.5417 | 0.6000 | 0.0000 | 0.0319 | 0.2244 |
| LLM-Predict + Fallback | 20 | 0.5000 | 0.5417 | 0.6000 | 0.0000 | 0.0319 | 0.2244 |
| Oracle Cache | 20 | 1.0000 | 0.9875 | 1.0000 | 0.0000 | 0.0511 | 0.4440 |

## Main Comparisons

Compared with `Random Cache`:
- precision: 0.5000 vs 0.0667, delta=+0.4333, rel=+650.0%
- recall: 0.5417 vs 0.1750, delta=+0.3667, rel=+209.5%
- hit_rate: 0.6000 vs 0.2000, delta=+0.4000, rel=+200.0%
- f1: 0.0319 vs 0.0145, delta=+0.0174, rel=+119.9%
- rouge_l: 0.2244 vs 0.0488, delta=+0.1756, rel=+359.8%

Compared with `Recency Cache`:
- precision: 0.5000 vs 0.3500, delta=+0.1500, rel=+42.9%
- recall: 0.5417 vs 0.8042, delta=-0.2625, rel=-32.6%
- hit_rate: 0.6000 vs 1.0000, delta=-0.4000, rel=-40.0%
- f1: 0.0319 vs 0.0257, delta=+0.0061, rel=+23.9%
- rouge_l: 0.2244 vs 0.0798, delta=+0.1446, rel=+181.3%

Compared with `Reactive Vector Retrieval`:
- precision: 0.5000 vs 0.1000, delta=+0.4000, rel=+400.0%
- recall: 0.5417 vs 0.2750, delta=+0.2667, rel=+97.0%
- hit_rate: 0.6000 vs 0.3000, delta=+0.3000, rel=+100.0%
- f1: 0.0319 vs 0.0106, delta=+0.0213, rel=+200.6%
- rouge_l: 0.2244 vs 0.0631, delta=+0.1613, rel=+255.7%

Compared with `Reactive Graph Retrieval`:
- precision: 0.5000 vs 0.0500, delta=+0.4500, rel=+900.0%
- recall: 0.5417 vs 0.1500, delta=+0.3917, rel=+261.1%
- hit_rate: 0.6000 vs 0.1500, delta=+0.4500, rel=+300.0%
- f1: 0.0319 vs 0.0038, delta=+0.0281, rel=+736.3%
- rouge_l: 0.2244 vs 0.0250, delta=+0.1994, rel=+797.6%

Compared with `LLM-Predict Cache Only`:
- precision: 0.5000 vs 0.5000, delta=+0.0000, rel=+0.0%
- recall: 0.5417 vs 0.5417, delta=+0.0000, rel=+0.0%
- hit_rate: 0.6000 vs 0.6000, delta=+0.0000, rel=+0.0%
- f1: 0.0319 vs 0.0319, delta=+0.0000, rel=+0.0%
- rouge_l: 0.2244 vs 0.2244, delta=+0.0000, rel=+0.0%

## Verifier / Reranker Diagnosis
- Proactive precision: 0.1558
- Proactive recall: 0.7958
- Proactive hit_rate: 0.9500
- Prepared hit samples: 19/20 (95.0%)
- Prepared full-cover samples: 13/20 (65.0%)
- Final hit samples: 12/20 (60.0%)
- Final full-cover samples: 10/20 (50.0%)
- Selection losses: 7 samples where prepared hit but final missed
- Prepared misses: 1 samples where prepared missed all gold evidence
- Verifier providers: {'reranker:flagembedding': 20}
- Verifier decisions: {'use': 20}
- Reranker status: {'available': 20}

Representative selection losses:
- locomo_c01_tsqa_008: gold=m_032,m_048; prepared=m_048,m_047,m_046,m_028,m_025,m_035,m_027,m_040,m_007; verifier=m_035,m_028; final=m_035,m_028; provider=reranker:flagembedding
- locomo_c01_tsqa_010: gold=m_046; prepared=m_046,m_045,m_039,m_028,m_025,m_026,m_035,m_005,m_009,m_003; verifier=m_028; final=m_028; provider=reranker:flagembedding
- locomo_c01_tsqa_011: gold=m_048; prepared=m_048,m_047,m_046,m_028,m_025,m_035,m_027,m_040,m_007; verifier=m_007,m_028; final=m_007,m_028; provider=reranker:flagembedding
- locomo_c01_tsqa_012: gold=m_048,m_061; prepared=m_061,m_058,m_060,m_028,m_026,m_025,m_027,m_050,m_057; verifier=m_028; final=m_028; provider=reranker:flagembedding
- locomo_c01_tsqa_016: gold=m_012,m_018,m_080,m_175; prepared=m_175,m_038,m_109,m_028,m_025,m_071,m_144,m_085,m_041,m_103,m_167,m_122; verifier=m_025,m_167; final=m_025,m_167; provider=reranker:flagembedding

Representative prepared misses:
- locomo_c01_tsqa_017: gold=m_080; prepared=m_079,m_077,m_078,m_028,m_071,m_026,m_035,m_018,m_005,m_012; verifier=m_035; final=m_035; provider=reranker:flagembedding

Cache-only vs fallback:
- Cache-only recall=0.5417, fallback recall=0.5417
- Fallback rate=0.0000; if recall barely changes, fallback is not rescuing many misses.

## Literature-Level Check
- This run's answer F1 is 3.19 on a 0-100 scale.
- This run's evidence-selection recall is 54.17 on a 0-100 scale, with budget=3.
- Recency evidence recall in the same run is 80.42, much higher than the method result.
- Published LoCoMo papers usually report answer F1 / recall@k under their own retrieval units and k values, so the numbers are not strictly one-to-one comparable.
- The current run should not be claimed as surpassing paper-level LoCoMo results. It is below the original RAG-style LoCoMo answer-F1 range and also below recent dedicated memory-system reports.

## Bottom Line
- Current final precision=0.5000, recall=0.5417, hit_rate=0.6000.
- Compared with vector retrieval, recall delta=+0.2667.
- Compared with recency, recall delta=-0.2625.
- The method is not yet competitive with the strong recency baseline on this time-sliced setup.
