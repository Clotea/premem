# LoCoMo Result Analysis

- Result file: `server_results\locomo_time-sliced_qwen_vllm_bge_gpu1_limit0.json`
- Samples: 1986

## Method Summary
| method | samples | precision | recall | hit_rate | fallback_rate | answer_f1 | rouge_l |
|---|---:|---:|---:|---:|---:|---:|---:|
| Random Cache | 1986 | 0.0178 | 0.0439 | 0.0519 | 0.0000 | 0.0362 | 0.0855 |
| Recency Cache | 1986 | 0.3520 | 0.8919 | 0.9960 | 0.0000 | 0.0622 | 0.2646 |
| Reactive Vector Retrieval | 1986 | 0.0735 | 0.1966 | 0.2140 | 1.0000 | 0.0335 | 0.2080 |
| Reactive Graph Retrieval | 1986 | 0.0183 | 0.0452 | 0.0504 | 1.0000 | 0.0232 | 0.1095 |
| LLM-Predict Cache Only | 1986 | 0.1460 | 0.2469 | 0.2724 | 0.0000 | 0.0428 | 0.2570 |
| LLM-Predict + Fallback | 1986 | 0.1511 | 0.2544 | 0.2860 | 0.1984 | 0.0407 | 0.2585 |
| Oracle Cache | 1986 | 0.9975 | 0.9773 | 0.9975 | 0.0000 | 0.1393 | 0.5943 |

## Main Comparisons

Compared with `Random Cache`:
- precision: 0.1511 vs 0.0178, delta=+0.1333, rel=+749.1%
- recall: 0.2544 vs 0.0439, delta=+0.2105, rel=+480.0%
- hit_rate: 0.2860 vs 0.0519, delta=+0.2341, rel=+451.5%
- f1: 0.0407 vs 0.0362, delta=+0.0045, rel=+12.4%
- rouge_l: 0.2585 vs 0.0855, delta=+0.1730, rel=+202.3%

Compared with `Recency Cache`:
- precision: 0.1511 vs 0.3520, delta=-0.2009, rel=-57.1%
- recall: 0.2544 vs 0.8919, delta=-0.6375, rel=-71.5%
- hit_rate: 0.2860 vs 0.9960, delta=-0.7100, rel=-71.3%
- f1: 0.0407 vs 0.0622, delta=-0.0215, rel=-34.5%
- rouge_l: 0.2585 vs 0.2646, delta=-0.0061, rel=-2.3%

Compared with `Reactive Vector Retrieval`:
- precision: 0.1511 vs 0.0735, delta=+0.0775, rel=+105.5%
- recall: 0.2544 vs 0.1966, delta=+0.0579, rel=+29.4%
- hit_rate: 0.2860 vs 0.2140, delta=+0.0720, rel=+33.6%
- f1: 0.0407 vs 0.0335, delta=+0.0072, rel=+21.5%
- rouge_l: 0.2585 vs 0.2080, delta=+0.0505, rel=+24.3%

Compared with `Reactive Graph Retrieval`:
- precision: 0.1511 vs 0.0183, delta=+0.1328, rel=+725.7%
- recall: 0.2544 vs 0.0452, delta=+0.2092, rel=+462.7%
- hit_rate: 0.2860 vs 0.0504, delta=+0.2356, rel=+468.0%
- f1: 0.0407 vs 0.0232, delta=+0.0175, rel=+75.3%
- rouge_l: 0.2585 vs 0.1095, delta=+0.1490, rel=+136.0%

Compared with `LLM-Predict Cache Only`:
- precision: 0.1511 vs 0.1460, delta=+0.0050, rel=+3.4%
- recall: 0.2544 vs 0.2469, delta=+0.0075, rel=+3.1%
- hit_rate: 0.2860 vs 0.2724, delta=+0.0136, rel=+5.0%
- f1: 0.0407 vs 0.0428, delta=-0.0020, rel=-4.8%
- rouge_l: 0.2585 vs 0.2570, delta=+0.0015, rel=+0.6%

## Verifier / Reranker Diagnosis
- Proactive precision: 0.1740
- Proactive recall: 0.8141
- Proactive hit_rate: 0.9174
- Prepared hit samples: 1823/1986 (91.8%)
- Prepared full-cover samples: 1471/1986 (74.1%)
- Final hit samples: 568/1986 (28.6%)
- Final full-cover samples: 465/1986 (23.4%)
- Selection losses: 1265 samples where prepared hit but final missed
- Prepared misses: 158 samples where prepared missed all gold evidence
- Verifier providers: {'vllm': 1339, 'lexical': 647}
- Verifier decisions: {'use': 650, 'fallback': 394, 'partial_use': 942}
- Reranker status: {'unavailable': 1339, 'not_recorded': 647}
- Reranker unavailable reasons:
  - 1339: Reranker unavailable: RuntimeError: Invalid device string: '1'

Representative selection losses:
- locomo_c01_tsqa_002: gold=m_012; prepared=m_012,m_011,m_009,m_002; verifier=; final=m_004,m_006,m_008; provider=lexical
- locomo_c01_tsqa_008: gold=m_032,m_048; prepared=m_048,m_047,m_046,m_028,m_025,m_035,m_027,m_040,m_007; verifier=m_028,m_040,m_007; final=m_028,m_040,m_007; provider=vllm
- locomo_c01_tsqa_011: gold=m_048; prepared=m_048,m_047,m_046,m_028,m_025,m_035,m_027,m_040,m_007; verifier=m_028,m_007,m_035; final=m_028,m_007,m_035; provider=vllm
- locomo_c01_tsqa_012: gold=m_048,m_061; prepared=m_061,m_058,m_039,m_026,m_028,m_025; verifier=m_058,m_028,m_026; final=m_058,m_028,m_026; provider=vllm
- locomo_c01_tsqa_016: gold=m_012,m_018,m_080,m_175; prepared=m_175,m_038,m_109,m_028,m_025,m_071,m_144,m_085,m_041,m_103,m_167,m_122; verifier=m_025,m_109,m_167; final=m_025,m_109,m_167; provider=vllm

Representative prepared misses:
- locomo_c01_tsqa_017: gold=m_080; prepared=m_079,m_077,m_078,m_028,m_071,m_026,m_035,m_074,m_012; verifier=; final=m_015,m_080,m_006; provider=lexical
- locomo_c01_tsqa_022: gold=m_103; prepared=m_102,m_046,m_080,m_028,m_025,m_078,m_071,m_011,m_095; verifier=m_028,m_095,m_025; final=m_028,m_095,m_025; provider=vllm
- locomo_c01_tsqa_028: gold=m_113,m_117; prepared=m_116,m_115,m_096,m_028,m_071,m_025,m_009,m_085; verifier=m_028,m_071,m_009; final=m_028,m_071,m_009; provider=vllm
- locomo_c01_tsqa_041: gold=m_108,m_199; prepared=m_198,m_196,m_159,m_028,m_025,m_071,m_197,m_007; verifier=; final=m_169,m_151,m_130; provider=lexical
- locomo_c01_tsqa_047: gold=m_036,m_037,m_039,m_045,m_078,m_089,m_110,m_159,m_164,m_176,m_220,m_268,m_283,m_285,m_305,m_310; prepared=m_419,m_300,m_228,m_196,m_028,m_071,m_405,m_041,m_085,m_140,m_347,m_281; verifier=; final=m_283,m_089,m_151; provider=lexical

Cache-only vs fallback:
- Cache-only recall=0.2469, fallback recall=0.2544
- Fallback rate=0.1984; if recall barely changes, fallback is not rescuing many misses.

## Literature-Level Check
- This run's answer F1 is 4.07 on a 0-100 scale.
- This run's evidence-selection recall is 25.44 on a 0-100 scale, with budget=3.
- Recency evidence recall in the same run is 89.19, much higher than the method result.
- Published LoCoMo papers usually report answer F1 / recall@k under their own retrieval units and k values, so the numbers are not strictly one-to-one comparable.
- The current run should not be claimed as surpassing paper-level LoCoMo results. It is below the original RAG-style LoCoMo answer-F1 range and also below recent dedicated memory-system reports.

## Bottom Line
- Current final precision=0.1511, recall=0.2544, hit_rate=0.2860.
- Compared with vector retrieval, recall delta=+0.0579.
- Compared with recency, recall delta=-0.6375.
- The method is not yet competitive with the strong recency baseline on this time-sliced setup.
