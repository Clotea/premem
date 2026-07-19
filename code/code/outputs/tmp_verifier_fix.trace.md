# LoCoMo Trace Report

This report shows ground truth, prediction, gap reasoning, verifier choice, and final selection.

## Method Summary

| method | precision | recall | hit_rate | fallback_rate |
|---|---:|---:|---:|---:|
| Random Cache | 0.333 | 1.000 | 1.000 | 0.000 |
| Recency Cache | 0.333 | 1.000 | 1.000 | 0.000 |
| Reactive Vector Retrieval | 0.333 | 1.000 | 1.000 | 1.000 |
| Reactive Graph Retrieval | 0.333 | 1.000 | 1.000 | 1.000 |
| LLM-Predict Cache Only | 1.000 | 1.000 | 1.000 | 0.000 |
| LLM-Predict + Fallback | 1.000 | 1.000 | 1.000 | 0.000 |
| Oracle Cache | 1.000 | 1.000 | 1.000 | 0.000 |

---

## locomo_c01_tsqa_001

### Selection Snapshot

```text
Gold evidence: m_003
Prepared memories: m_002, m_003, m_001
Verifier selected: m_003
Final selected: m_003
```

### Question / Ground Truth

- Question: When did Caroline go to the LGBTQ support group?
- Gold answer: 7 May 2023
- Gold evidence turn ids: D1:3
- Gold evidence memory ids: m_003
- History turns: 3

Gold evidence memories:
- m_003 turn=D1:3 time=3 :: Caroline: I went to a LGBTQ support group yesterday and it was so powerful.

### Prediction / Prepared Cache

- Predicted future intents: The user may continue the active segment., The user may ask about recently mentioned decisions, entities, or metrics.
- Prediction provider metadata: `{'provider': 'heuristic'}`

Activated memories from predictor:
- m_002 turn=D1:2 time=2 :: Melanie: Hey Caroline! Good to see you! I'm swamped with the kids & work. What's up with you? Anything new?
- m_003 turn=D1:3 time=3 :: Caroline: I went to a LGBTQ support group yesterday and it was so powerful.
- m_001 turn=D1:1 time=1 :: Caroline: Hey Mel! Good to see you! How have you been?

Cache memories after insertion:
- m_002 turn=D1:2 time=2 :: Melanie: Hey Caroline! Good to see you! I'm swamped with the kids & work. What's up with you? Anything new?
- m_003 turn=D1:3 time=3 :: Caroline: I went to a LGBTQ support group yesterday and it was so powerful.
- m_001 turn=D1:1 time=1 :: Caroline: Hey Mel! Good to see you! How have you been?

### Gap Reasoning

- Context package: ctx_locomo_c01_tsqa_001_through_d1_3
- Target intent: The user may continue the active segment.
- Possible user query: The user may ask about recently mentioned decisions, entities, or metrics.
- Support check: `{'support_status': 'partial', 'supported_claims': ['The user may ask about recently mentioned decisions, entities, or metrics.'], 'missing_support': ['user goal', 'method definition', 'related work distinction', 'evidence'], 'confidence': 0.488, 'provider': 'vllm'}`

Selected meta paths:
- path_id=P1; reason=This path helps in recovering the user's goal, method definition, and active idea which aligns with the intent of the user as described in the dialogue.

Executed paths:
- path_id=P1; selected_memory_ids=['m_002', 'm_003', 'm_001']; node_count=15; edge_count=18

Gaps:
- gap_id=gap_001; gap_type=evidence_gap; missing_support=user goal; priority=0.8; repair_query=user goal may active ask continue decisions definition distinction entities evidence mentioned
- gap_id=gap_002; gap_type=definition_gap; missing_support=method definition; priority=0.8; repair_query=user definition may method active ask continue decisions distinction entities evidence goal
- gap_id=gap_003; gap_type=evidence_gap; missing_support=related work distinction; priority=0.8; repair_query=user distinction may related work active ask continue decisions definition entities evidence
- gap_id=gap_004; gap_type=evidence_gap; missing_support=evidence; priority=0.8; repair_query=user evidence may active ask continue decisions definition distinction entities goal mentioned

Repair evidence:
- evidence_id=ev_001; candidate_gap_id=gap_001; source_id=m_002; chunk_id=D1:2; score=0.065; content=Melanie: Hey Caroline! Good to see you! I'm swamped with the kids & work. What's up with you? Anything new?
- evidence_id=ev_002; candidate_gap_id=gap_001; source_id=m_003; chunk_id=D1:3; score=0.0106; content=Caroline: I went to a LGBTQ support group yesterday and it was so powerful.
- evidence_id=ev_003; candidate_gap_id=gap_002; source_id=m_002; chunk_id=D1:2; score=0.065; content=Melanie: Hey Caroline! Good to see you! I'm swamped with the kids & work. What's up with you? Anything new?
- evidence_id=ev_004; candidate_gap_id=gap_002; source_id=m_003; chunk_id=D1:3; score=0.0106; content=Caroline: I went to a LGBTQ support group yesterday and it was so powerful.
- evidence_id=ev_005; candidate_gap_id=gap_003; source_id=m_002; chunk_id=D1:2; score=0.065; content=Melanie: Hey Caroline! Good to see you! I'm swamped with the kids & work. What's up with you? Anything new?
- evidence_id=ev_006; candidate_gap_id=gap_003; source_id=m_003; chunk_id=D1:3; score=0.0106; content=Caroline: I went to a LGBTQ support group yesterday and it was so powerful.
- evidence_id=ev_007; candidate_gap_id=gap_004; source_id=m_002; chunk_id=D1:2; score=0.065; content=Melanie: Hey Caroline! Good to see you! I'm swamped with the kids & work. What's up with you? Anything new?
- evidence_id=ev_008; candidate_gap_id=gap_004; source_id=m_003; chunk_id=D1:3; score=0.0106; content=Caroline: I went to a LGBTQ support group yesterday and it was so powerful.

Evidence bindings:
- evidence_id=ev_001; bind_to=gap_001; binding_type=supports; reason=The conversation snippet mentions recent activities which can be used to clarify the user's recent decisions or entities.

Prepared memories:
- m_002 turn=D1:2 time=2 :: Melanie: Hey Caroline! Good to see you! I'm swamped with the kids & work. What's up with you? Anything new?
- m_003 turn=D1:3 time=3 :: Caroline: I went to a LGBTQ support group yesterday and it was so powerful.
- m_001 turn=D1:1 time=1 :: Caroline: Hey Mel! Good to see you! How have you been?

### Verifier

- Decision: use
- Provider: vllm
- Confidence: 0.9
- Reason: Memory m_003 directly answers the query by providing information about Caroline going to an LGBTQ support group.
- Verifier selected ids: m_003

Verifier memory candidates:
- id=m_002; source_turn_id=D1:2; path_id=P1; summary=Melanie: Hey Caroline! Good to see you! I'm swamped with the kids & work. What's up with you? Anything new?
- id=m_003; source_turn_id=D1:3; path_id=P1; summary=Caroline: I went to a LGBTQ support group yesterday and it was so powerful.
- id=m_001; source_turn_id=D1:1; path_id=P1; summary=Caroline: Hey Mel! Good to see you! How have you been?

Verifier selected memories:
- m_003 turn=D1:3 time=3 :: Caroline: I went to a LGBTQ support group yesterday and it was so powerful.

### Final Selection

- Cache-only selected ids: m_003
- Cache-only metrics: precision=1.000, recall=1.000, hit=1.000
- Cache+fallback selected ids: m_003
- Cache+fallback fallback used: False
- Cache+fallback metrics: precision=1.000, recall=1.000, hit=1.000
- Proactive metrics before verifier: precision=0.333, recall=1.000, hit=1.000

Final selected memories:
- m_003 turn=D1:3 time=3 :: Caroline: I went to a LGBTQ support group yesterday and it was so powerful.
