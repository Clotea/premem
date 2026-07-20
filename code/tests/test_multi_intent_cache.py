from __future__ import annotations

import unittest

from code.graph_store import NODE_FACT, NODE_INTENT, build_memory_graph
from code.locomo import build_trace_report
from code.multi_intent_cache import (
    build_multi_intent_bundle,
    merge_with_reactive_results,
    route_multi_intent_query,
)
from code.pipeline import memory_writer, run_evaluation
from code.utils import ActivatedMemory, Prediction, Sample, Turn


def demo_history() -> list[Turn]:
    return [
        Turn(
            id="D1:1",
            timestamp=1,
            speaker="Alice",
            segment_id="session_1",
            segment_summary="Alice discussed weekend activities.",
            text="I went hiking at Green Lake on Saturday.",
        ),
        Turn(
            id="D1:2",
            timestamp=2,
            speaker="Bob",
            segment_id="session_1",
            segment_summary="Alice discussed weekend activities.",
            text="That sounds fun. How did the hike make you feel?",
        ),
        Turn(
            id="D1:3",
            timestamp=3,
            speaker="Alice",
            segment_id="session_1",
            segment_summary="Alice discussed weekend activities.",
            text="I felt relaxed, and on Sunday I visited my friend Carol.",
        ),
    ]


class FixedPredictor:
    name = "fixed"

    def predict(self, history, memory_nodes, graph, budget):
        return Prediction(
            predicted_future_intents=[
                "share recent activities",
                "talk about feelings",
                "continue conversation",
            ],
            activated_memory_ids=[
                ActivatedMemory(id="m_003", confidence=0.9, reason="recent activity"),
                ActivatedMemory(id="m_001", confidence=0.8, reason="earlier activity"),
            ],
        )


def demo_config() -> dict:
    return {
        "cache_budget": 2,
        "retrieval_top_k": 2,
        "fallback_retriever": "vector",
        "gap_reasoning": {"enabled": False},
        "qa_reader": {"use_for_all_methods": False},
        "multi_intent_cache": {
            "enabled": True,
            "max_heads": 3,
            "global_cache_budget": 0,
            "per_head_candidate_k": 3,
            "embedding_provider": "hashing",
            "embedding_dimensions": 128,
            "route_threshold": 0.12,
            "native_rag_threshold": 0.06,
            "multi_head_threshold": 0.10,
            "readiness_threshold": 0.20,
            "semantic_support_threshold": 0.04,
        },
    }


class MultiIntentCacheTests(unittest.TestCase):
    def test_partial_repair_never_evicts_prepared_memories(self) -> None:
        memories = memory_writer(demo_history())
        merged, repair_ids = merge_with_reactive_results(
            prepared_ids=["m_003", "m_001"],
            reactive_memories=[memories[0], memories[1], memories[2]],
            memory_nodes=memories,
            limit=3,
        )

        self.assertEqual([memory.id for memory in merged], ["m_003", "m_001", "m_002"])
        self.assertEqual(repair_ids, ["m_002"])

    def test_idle_planner_materializes_intents_and_respects_global_budget(self) -> None:
        history = demo_history()
        memories = memory_writer(history)
        graph = build_memory_graph(memories, history, similarity_threshold=0.1)
        prediction = FixedPredictor().predict(history, memories, graph, budget=2)

        bundle = build_multi_intent_bundle(
            "demo_context",
            history,
            memories,
            graph,
            prediction,
            demo_config(),
        )

        self.assertEqual(len(bundle["intent_heads"]), 3)
        self.assertLessEqual(len(bundle["physical_memory_ids"]), 2)
        self.assertGreater(bundle["logical_branch_memory_count"], len(bundle["physical_memory_ids"]))
        for head in bundle["intent_heads"]:
            node = graph.nodes[head["id"]]
            self.assertEqual(node["node_type"], NODE_INTENT)
            self.assertTrue(node["is_intent"])
            self.assertTrue(node["embedding"])
        self.assertTrue(
            any(node.get("node_type") == NODE_FACT for node in graph.nodes.values())
        )

    def test_query_router_uses_relevant_head_and_rejects_unrelated_query(self) -> None:
        history = demo_history()
        memories = memory_writer(history)
        graph = build_memory_graph(memories, history, similarity_threshold=0.1)
        prediction = FixedPredictor().predict(history, memories, graph, budget=2)
        config = demo_config()
        bundle = build_multi_intent_bundle(
            "demo_context",
            history,
            memories,
            graph,
            prediction,
            config,
        )

        relevant = route_multi_intent_query(
            "What activities did Alice do recently?",
            bundle,
            memories,
            config,
        )
        unrelated = route_multi_intent_query(
            "What will the weather be tomorrow in Tokyo?",
            bundle,
            memories,
            config,
        )

        self.assertNotEqual(relevant["decision"], "native_rag")
        self.assertIn("intent_", relevant["selected_head_ids"][0])
        self.assertEqual(unrelated["decision"], "native_rag")

    def test_pipeline_keeps_old_method_and_adds_multi_intent_ablation(self) -> None:
        history = demo_history()
        sample = Sample(
            id="demo_sample",
            history=history,
            history_cache_key="demo_context",
            question="What did Alice do on Saturday?",
            answer="She went hiking at Green Lake.",
            gold_evidence_turn_ids=["D1:1"],
        )

        summary = run_evaluation(
            [sample],
            demo_config(),
            FixedPredictor(),
            llm_client=None,
        )
        by_method = {row["method"]: row for row in summary}

        self.assertIn("Pre-query Prepared + Reader", by_method)
        self.assertIn("Multi-Intent Prepared + Adaptive Router", by_method)
        multi_sample = by_method["Multi-Intent Prepared + Adaptive Router"]["samples"][0]
        self.assertFalse(multi_sample["uses_query_for_preparation"])
        self.assertTrue(multi_sample["uses_query_for_routing"])
        self.assertLessEqual(multi_sample["prepared_physical_cache_size"], 2)
        trace_text = "\n".join(build_trace_report(summary))
        self.assertIn("多意图缓存规划与路由", trace_text)
        self.assertIn("share recent activities", trace_text)
        self.assertIn("Golden Memory", trace_text)


if __name__ == "__main__":
    unittest.main()
