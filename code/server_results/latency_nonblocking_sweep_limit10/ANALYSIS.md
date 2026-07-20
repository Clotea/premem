# Non-blocking pre-query lead-time sweep (head-10)

This result treats idle lead time as a controlled variable; it is not inferred
from LoCoMo. At query arrival the prepared-cache snapshot is frozen. Incomplete
planning never blocks the query: missing memories immediately use Reactive
fallback.

Configuration: Top-5, gold working set, four prefetch workers, deterministic
40–500 ms heavy-tail memory-load model, Qwen2.5-7B streaming generation.

| Lead time | Planning ready | Ready | Full hit | Fallback | p95 stall | p95 TTFT |
|---:|---:|---:|---:|---:|---:|---:|
| 0–5s | 0% | 0% | 0% | 100% | 191.7ms | 248.5ms |
| 10s | 20% | 20% | 20% | 80% | 191.7ms | 248.5ms |
| 15s | 40% | 30% | 30% | 70% | 164.9ms | 221.3ms |
| 20s | 60% | 60% | 60% | 40% | 164.9ms | 221.3ms |
| 25–60s | 100% | 95% | 90% | 10% | 109.1ms | 166.2ms |

At the selected 500 ms point, non-blocking Predictive behaves like Reactive:
p95 TTFT is 248.9 ms versus 245.7 ms, rather than waiting tens of seconds for
planning. Oracle p95 TTFT is 57.4 ms, so the storage/TTFT headroom exists, but
the current 16–25 second planning path cannot exploit short lead times.

The sample count is only ten. Percentiles, especially the p95 interpolation
with one remaining miss at 90% full-hit, require a stratified 100+ sample run.
