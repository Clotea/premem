# Planning trace

- Sample: `locomo_c01_tsqa_016`
- History turns: 175
- Graph: 489 nodes / 1261 edges
- Selected conceptual paths: P1
- Prepared cache: m_028, m_137, m_175, m_038, m_109
- Gold memories: m_012, m_018, m_080, m_175
- Prepared recall: 0.250
- Full cover: 0.000
- Total prediction + planning latency: 16766.518 ms

## Important implementation finding

The selected P1-P6 path changes the global memory ranking bonus. Only after top-k selection does the code use physical graph edges to expand an incident-edge local subgraph; it does not traverse the conceptual Intent/UserGoal/Paper/Claim/Evidence ontology hop by hop.

## Planning stages

| Stage | Latency (ms) |
|---|---:|
| Materialize predicted intent | 0.134 |
| Select conceptual meta-paths | 961.812 |
| Execute path-conditioned memory activation | 15.133 |
| Check whether activated graph supports the intent | 2301.986 |
| Generate missing-support GapNodes | 5601.825 |
| Retrieve evidence for each gap | 33.346 |
| Bind evidence to GapNodes | 2804.368 |
| Merge predictor, path, and repair candidates | 0.134 |
| Compress candidates into the prepared cache | 12.070 |
