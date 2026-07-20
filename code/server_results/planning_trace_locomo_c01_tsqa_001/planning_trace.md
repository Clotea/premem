# Planning trace

- Sample: `locomo_c01_tsqa_001`
- History turns: 3
- Graph: 15 nodes / 21 edges
- Selected conceptual paths: P1
- Prepared cache: m_003, m_002, m_001
- Gold memories: m_003
- Prepared recall: 1.000
- Full cover: 1.000
- Total prediction + planning latency: 15342.507 ms

## Important implementation finding

The selected P1-P6 path changes the global memory ranking bonus. Only after top-k selection does the code use physical graph edges to expand an incident-edge local subgraph; it does not traverse the conceptual Intent/UserGoal/Paper/Claim/Evidence ontology hop by hop.

## Planning stages

| Stage | Latency (ms) |
|---|---:|
| Materialize predicted intent | 0.282 |
| Select conceptual meta-paths | 1327.409 |
| Execute path-conditioned memory activation | 0.712 |
| Check whether activated graph supports the intent | 1878.848 |
| Generate missing-support GapNodes | 5516.267 |
| Retrieve evidence for each gap | 1.194 |
| Bind evidence to GapNodes | 2746.815 |
| Merge predictor, path, and repair candidates | 0.072 |
| Compress candidates into the prepared cache | 0.039 |
