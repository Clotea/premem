# Planning trace

- Sample: `locomo_c01_tsqa_019`
- History turns: 167
- Graph: 470 nodes / 1197 edges
- Selected conceptual paths: P1, P4, P5, P6
- Prepared cache: m_167, m_103, m_046, m_066, m_108
- Gold memories: m_064, m_108, m_167
- Prepared recall: 0.667
- Full cover: 0.000
- Total prediction + planning latency: 23580.475 ms

## Important implementation finding

The selected P1-P6 path changes the global memory ranking bonus. Only after top-k selection does the code use physical graph edges to expand an incident-edge local subgraph; it does not traverse the conceptual Intent/UserGoal/Paper/Claim/Evidence ontology hop by hop.

## Planning stages

| Stage | Latency (ms) |
|---|---:|
| Materialize predicted intent | 0.180 |
| Select conceptual meta-paths | 3921.269 |
| Execute path-conditioned memory activation | 39.152 |
| Check whether activated graph supports the intent | 2151.397 |
| Generate missing-support GapNodes | 9993.736 |
| Retrieve evidence for each gap | 30.334 |
| Bind evidence to GapNodes | 1798.786 |
| Merge predictor, path, and repair candidates | 0.110 |
| Compress candidates into the prepared cache | 12.734 |
