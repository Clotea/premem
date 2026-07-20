# Planning trace

- Sample: `locomo_c01_tsqa_003`
- History turns: 11
- Graph: 37 nodes / 71 edges
- Selected conceptual paths: P1
- Prepared cache: m_009, m_002, m_011, m_004, m_010
- Gold memories: m_009, m_011
- Prepared recall: 1.000
- Full cover: 1.000
- Total prediction + planning latency: 21059.205 ms

## Important implementation finding

The selected P1-P6 path changes the global memory ranking bonus. Only after top-k selection does the code use physical graph edges to expand an incident-edge local subgraph; it does not traverse the conceptual Intent/UserGoal/Paper/Claim/Evidence ontology hop by hop.

## Planning stages

| Stage | Latency (ms) |
|---|---:|
| Materialize predicted intent | 0.143 |
| Select conceptual meta-paths | 1619.399 |
| Execute path-conditioned memory activation | 1.324 |
| Check whether activated graph supports the intent | 1911.409 |
| Generate missing-support GapNodes | 9953.326 |
| Retrieve evidence for each gap | 2.837 |
| Bind evidence to GapNodes | 2492.436 |
| Merge predictor, path, and repair candidates | 0.085 |
| Compress candidates into the prepared cache | 4.213 |
