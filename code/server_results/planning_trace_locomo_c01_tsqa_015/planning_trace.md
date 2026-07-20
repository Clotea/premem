# Planning trace

- Sample: `locomo_c01_tsqa_015`
- History turns: 73
- Graph: 226 nodes / 522 edges
- Selected conceptual paths: P1, P3, P4, P6
- Prepared cache: m_071, m_028, m_069, m_073, m_072
- Gold memories: m_040, m_073
- Prepared recall: 0.500
- Full cover: 0.000
- Total prediction + planning latency: 25185.996 ms

## Important implementation finding

The selected P1-P6 path changes the global memory ranking bonus. Only after top-k selection does the code use physical graph edges to expand an incident-edge local subgraph; it does not traverse the conceptual Intent/UserGoal/Paper/Claim/Evidence ontology hop by hop.

## Planning stages

| Stage | Latency (ms) |
|---|---:|
| Materialize predicted intent | 0.298 |
| Select conceptual meta-paths | 5352.990 |
| Execute path-conditioned memory activation | 22.993 |
| Check whether activated graph supports the intent | 2298.276 |
| Generate missing-support GapNodes | 9320.991 |
| Retrieve evidence for each gap | 18.968 |
| Bind evidence to GapNodes | 2574.503 |
| Merge predictor, path, and repair candidates | 0.083 |
| Compress candidates into the prepared cache | 11.832 |
