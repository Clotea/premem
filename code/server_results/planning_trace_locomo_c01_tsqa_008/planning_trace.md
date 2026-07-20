# Planning trace

- Sample: `locomo_c01_tsqa_008`
- History turns: 48
- Graph: 149 nodes / 341 edges
- Selected conceptual paths: P1
- Prepared cache: m_028, m_038, m_048, m_046, m_047
- Gold memories: m_032, m_048
- Prepared recall: 0.500
- Full cover: 0.000
- Total prediction + planning latency: 22889.891 ms

## Important implementation finding

The selected P1-P6 path changes the global memory ranking bonus. Only after top-k selection does the code use physical graph edges to expand an incident-edge local subgraph; it does not traverse the conceptual Intent/UserGoal/Paper/Claim/Evidence ontology hop by hop.

## Planning stages

| Stage | Latency (ms) |
|---|---:|
| Materialize predicted intent | 0.296 |
| Select conceptual meta-paths | 1449.669 |
| Execute path-conditioned memory activation | 7.012 |
| Check whether activated graph supports the intent | 2534.657 |
| Generate missing-support GapNodes | 9898.158 |
| Retrieve evidence for each gap | 14.498 |
| Bind evidence to GapNodes | 2565.697 |
| Merge predictor, path, and repair candidates | 0.072 |
| Compress candidates into the prepared cache | 9.794 |
