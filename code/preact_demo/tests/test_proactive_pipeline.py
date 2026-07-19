from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from preact_demo.code.embeddings import HashEmbeddingProvider
from preact_demo.code.external_search import search_memory_gaps
from preact_demo.code.graph_store import build_memory_graph
from preact_demo.code.grounding import ground_hypotheses
from preact_demo.code.memory_gap import detect_memory_gaps
from preact_demo.code.main import _load_simple_yaml, apply_overrides, load_config
from preact_demo.code.pipeline import memory_writer, prepare_working_context, run_evaluation
from preact_demo.code.predictors import HeuristicPredictor
from preact_demo.code.utils import FutureNeedHypothesis, MemoryGap, Sample, Turn
from preact_demo.code.working_context import build_working_context_package, verify_working_context


def demo_history() -> list[Turn]:
    return [
        Turn(
            id="t_001",
            timestamp=1,
            speaker="user",
            segment_id="seg_paper",
            segment_summary="ACL paper planning",
            text="I prefer submitting the memory paper to ACL main rather than Findings.",
        ),
        Turn(
            id="t_002",
            timestamp=2,
            speaker="assistant",
            segment_id="seg_paper",
            segment_summary="ACL paper planning",
            text="We should compare the approach with Mem0 and A-MEM and report retrieval latency.",
        ),
    ]


def demo_config() -> dict:
    return {
        "cache_budget": 2,
        "retrieval_top_k": 2,
        "fallback_retriever": "vector",
        "similarity_threshold": 0.1,
        "verifier_threshold": 0.08,
        "random_seed": 7,
        "embedding": {"provider": "hash", "dimensions": 128},
        "grounding": {
            "mode": "embedding",
            "activation_threshold": 0.01,
            "candidates_per_hypothesis": 2,
        },
        "memory_gap": {"mode": "score", "coverage_threshold": 0.15},
        "external_search": {"enabled": False},
        "verifier": {
            "mode": "embedding",
            "threshold": 0.08,
            "hypothesis_threshold": 0.08,
        },
        "answer_with_vllm": False,
    }


class FakeSearchProvider:
    name = "fake"

    def search(self, query: str, source_type: str, limit: int) -> list[dict]:
        return [
            {
                "title": f"{source_type} result for {query}",
                "url": f"https://example.test/{source_type}",
                "snippet": "External evidence about memory systems.",
            }
        ][:limit]


class ProactivePipelineTests(unittest.TestCase):
    def test_predictor_emits_structured_hypotheses(self) -> None:
        history = demo_history()
        memories = memory_writer(history)
        graph = build_memory_graph(memories, history)
        prediction = HeuristicPredictor().predict(history, memories, graph, budget=2)

        self.assertGreaterEqual(len(prediction.hypotheses), 1)
        self.assertTrue(prediction.hypotheses[0].intent)
        self.assertTrue(prediction.hypotheses[0].search_queries)

    def test_grounding_and_gap_detection_are_separate_stages(self) -> None:
        history = demo_history()
        memories = memory_writer(history)
        graph = build_memory_graph(memories, history)
        hypotheses = [
            FutureNeedHypothesis(
                id="h_acl",
                intent="Recall the ACL main submission preference.",
                search_queries=["ACL main Findings preference"],
            ),
            FutureNeedHypothesis(
                id="h_weather",
                intent="Find tomorrow's weather in Tokyo.",
                search_queries=["Tokyo weather tomorrow"],
            ),
        ]
        config = demo_config()
        embedding = HashEmbeddingProvider(128)
        grounded = ground_hypotheses(
            hypotheses, memories, graph, 2, embedding, config
        )
        gaps = detect_memory_gaps(hypotheses, grounded, memories, config)

        self.assertTrue(any(item.memory_id == "m_001" for item in grounded))
        self.assertEqual({item.hypothesis_id for item in gaps}, {"h_acl", "h_weather"})
        weather_gap = next(item for item in gaps if item.hypothesis_id == "h_weather")
        self.assertTrue(weather_gap.exists)

    def test_gap_search_supports_web_and_paper_sources(self) -> None:
        gaps = [
            MemoryGap(
                hypothesis_id="h_001",
                exists=True,
                reason="missing",
                search_queries=["proactive memory grounding"],
            )
        ]
        config = {
            "external_search": {
                "enabled": True,
                "source_types": ["web", "paper"],
                "results_per_query": 1,
                "max_queries_per_gap": 1,
            }
        }
        evidence = search_memory_gaps(gaps, config, FakeSearchProvider())

        self.assertEqual({item.source_type for item in evidence}, {"web", "paper"})

    def test_working_context_verifier_accepts_relevant_query(self) -> None:
        history = demo_history()
        memories = memory_writer(history)
        graph = build_memory_graph(memories, history)
        config = demo_config()
        embedding = HashEmbeddingProvider(128)
        hypothesis = FutureNeedHypothesis(
            id="h_001",
            intent="Recall ACL main submission preference.",
            search_queries=["ACL main Findings"],
        )
        grounded = ground_hypotheses(
            [hypothesis], memories, graph, 2, embedding, config
        )
        gaps = detect_memory_gaps([hypothesis], grounded, memories, config)
        package = build_working_context_package(
            "wcp_test", [hypothesis], grounded, gaps, [], memories, graph
        )
        verified = verify_working_context(
            "Why did I prefer ACL main over Findings?",
            package,
            memories,
            embedding,
            config,
        )

        self.assertTrue(verified["sufficient"])
        self.assertIn("m_001", verified["selected_memory_ids"])

        irrelevant = verify_working_context(
            "What will the weather be in Tokyo tomorrow?",
            package,
            memories,
            embedding,
            config,
        )
        self.assertFalse(irrelevant["sufficient"])

    def test_end_to_end_pipeline_records_package_and_fallback(self) -> None:
        config = demo_config()
        sample = Sample(
            id="sample_001",
            history=demo_history(),
            question="What was my ACL venue preference?",
            answer="ACL main rather than Findings.",
            evidence_terms=["ACL", "Findings", "preference"],
        )
        predictor = HeuristicPredictor()
        context = prepare_working_context("sample_001", sample.history, config, predictor)
        summary = run_evaluation([sample], config, predictor)
        proactive = next(item for item in summary if item["method"] == "LLM-Predict + Fallback")

        self.assertTrue(context["working_context"].hypotheses)
        self.assertTrue(context["working_context"].local_subgraph["nodes"])
        self.assertEqual(len(proactive["samples"]), 1)
        self.assertIn("working_context_package_id", proactive["samples"][0])

    def test_yaml_config_supports_vllm_port_and_model_overrides(self) -> None:
        with TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            config_path.write_text(
                "\n".join(
                    [
                        "cache_budget: 4",
                        "predictor: vllm",
                        "llm:",
                        "  host: 0.0.0.0",
                        "  port: 9001",
                        "  model: test-model",
                        "  fallback_to_heuristic: true",
                    ]
                ),
                encoding="utf-8",
            )
            config = load_config(str(config_path))
            parsed_without_pyyaml = _load_simple_yaml(config_path)

        self.assertEqual(config["cache_budget"], 4)
        self.assertEqual(config["llm"]["base_url"], "http://0.0.0.0:9001/v1")
        self.assertEqual(config["llm"]["model"], "test-model")
        self.assertEqual(parsed_without_pyyaml["llm"]["port"], 9001)

        apply_overrides(
            config,
            SimpleNamespace(
                predictor=None,
                cache_budget=None,
                retrieval_top_k=None,
                fallback_retriever=None,
                answer_with_vllm=False,
                enable_external_search=False,
                vllm_host="127.0.0.1",
                vllm_port=8123,
                vllm_url=None,
                vllm_model="override-model",
            ),
        )

        self.assertEqual(config["llm"]["base_url"], "http://127.0.0.1:8123/v1")
        self.assertEqual(config["llm"]["model"], "override-model")


if __name__ == "__main__":
    unittest.main()
