import unittest

from code.benchmark_latency import (
    MemoryCostModel,
    compute_nonblocking_deadline_sweep,
)
from code.utils import MemoryNode


class LatencyDeadlineSweepTests(unittest.TestCase):
    def test_incomplete_planning_falls_back_without_waiting_for_overrun(self):
        memory = MemoryNode(
            id="m_001",
            memory_type="fact",
            content="A short memory.",
            summary="A short memory.",
            keywords=[],
            entities=[],
            segment_id="s1",
            source_turn_id="t1",
            timestamp=1,
        )
        cost = MemoryCostModel(
            base_ms=40,
            tail_ms=0,
            per_kb_ms=0,
            max_ms=500,
            seed=7,
        )
        result = compute_nonblocking_deadline_sweep(
            windows_ms=[500, 2000],
            planning_ms=1000,
            working_ids=["m_001"],
            gold_ids=["m_001"],
            prefetched_ids=["m_001"],
            by_id={"m_001": memory},
            cost_model=cost,
            prefetch_concurrency=1,
            measured_retrieval_ms=2,
            query_encoding_ms=20,
            query_rerank_ms=30,
            prompt_build_ms=1,
            vllm_ttft_ms=50,
            vllm_e2e_ms=100,
        )
        early = result["500ms"]
        self.assertFalse(early["planning_ready"])
        self.assertTrue(early["fallback_used"])
        self.assertEqual(early["memory_stall_ms"], 92)
        self.assertEqual(early["ttft_ms"], 143)
        late = result["2000ms"]
        self.assertTrue(late["planning_ready"])
        self.assertFalse(late["fallback_used"])
        self.assertEqual(late["ready"], 1.0)
        self.assertEqual(late["memory_stall_ms"], 0.0)


if __name__ == "__main__":
    unittest.main()
