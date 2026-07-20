# Reader protocol v2 — LoCoMo head-10 regression

Environment: `server3`, `/home/yanghaotong/premem/exp/mem`, Qwen2.5-7B via
vLLM port 30000, BGE reranker on `cuda:1`.

Canonical result: `reader_protocol_v2_limit10_official.json` (re-scored with
the same NLTK `PorterStemmer` backend used by the official LoCoMo evaluator).

## Comparable before/after

| Method | Old official F1 | New official F1 | Delta | Recall | Full cover |
|---|---:|---:|---:|---:|---:|
| Pre-query Prepared + Reader | 0.1594 | 0.5822 | +0.4229 | 0.95 | 0.90 |
| Multi-Intent + Router | 0.1594 | 0.5822 | +0.4229 | 0.95 | 0.90 |
| Oracle / MemoryNode Oracle | 0.3127 | 0.7022 | +0.3895 | 1.00 | 1.00 |

The main method's query-time prepared-cache read remains approximately
`0.013 ms`. Its temporal F1 is `0.80`; Mem0-style judge is `0.60`, and the
strict judge is `0.50`.

## Diagnosis

- Six temporal questions improve from an old mean official F1 of about `0.095`
  to `0.80`. Session dates and deterministic relative-time resolution fixed
  the dominant Reader protocol error.
- Retrieval metrics do not change (`recall=0.95`, `full-cover=0.90`) while
  official F1 rises by 42.29 points. This isolates the old head-10 failure to
  Reader/data protocol, not intent or metapath recall.
- The remaining main-to-Oracle gap is `0.12` official F1. Most of it is the
  relationship-status sample: the main cache covers only one of two evidence
  turns, while both Oracles answer `Single`.
- Two Raw Oracle failures are not retrieval failures:
  - the sunrise image evidence contains no explicit `2022`;
  - “transgender stories” does not explicitly entail “transgender woman”.
- The charity-race annotation says Sunday while the source says last Saturday.
  Deterministic resolution preserves Saturday; this is a dataset-label
  inconsistency, not a metapath error.
- The education answer is semantically accepted by the Mem0-style judge but
  loses token F1 and fails the strict all-facts judge. This is a Reader
  specificity/evaluation-protocol difference.

## Scope warning

This run intentionally reuses the same first 10 samples for a clean before/after
comparison. Six are temporal and all come from conversation 1. It is a
regression result, not the final paper number. Run at least:

```bash
python locomo.py --limit 100 --sample-mode stratified --sample-seed 7 \
  --eval-mode time-sliced
```

before deciding whether to tune metapath, intent prediction, or the router.
