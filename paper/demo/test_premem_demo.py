import json
import unittest
from pathlib import Path

from premem_demo import (
    evaluate,
    heuristic_prediction,
    load_scenario,
    rank_graph,
    validate_predictions,
    visible_scenario,
)


SCENARIO = Path(__file__).with_name("scenario.json")


class PreMemDemoTest(unittest.TestCase):
    def setUp(self) -> None:
        self.data = load_scenario(SCENARIO)
        self.visible = visible_scenario(self.data)

    def test_hidden_query_is_not_visible_to_predictor(self) -> None:
        serialized = json.dumps(self.visible, ensure_ascii=False)
        self.assertNotIn("hidden_next_query", self.visible)
        self.assertNotIn(self.data["hidden_next_query"], serialized)
        self.assertNotIn("gold_memory_ids", self.visible)

    def test_offline_pipeline_returns_ranked_memory(self) -> None:
        prediction = heuristic_prediction(self.visible)
        valid_ids = {node["id"] for node in self.visible["memory_nodes"]}
        cleaned = validate_predictions(prediction, valid_ids)
        ranked = rank_graph(self.visible, cleaned)
        self.assertGreaterEqual(len(ranked), 3)
        metrics = evaluate(self.data, ranked, [], top_k=3)
        self.assertGreaterEqual(metrics["recall_at_k"], 0.0)
        self.assertLessEqual(metrics["recall_at_k"], 1.0)

    def test_flat_array_model_output_is_normalized(self) -> None:
        flat_output = {
            "intent": ["比较酒店", "规划餐饮"],
            "pseudo_query": ["两家酒店怎么选？", "附近吃什么？"],
            "probability": [0.7, 0.2],
            "entities": ["酒店", "会场"],
            "seed_memory_ids": ["m_hotel_shortlist"],
        }
        valid_ids = {node["id"] for node in self.visible["memory_nodes"]}
        cleaned = validate_predictions(flat_output, valid_ids)
        self.assertEqual(len(cleaned), 2)
        self.assertEqual(cleaned[0]["seed_memory_ids"], ["m_hotel_shortlist"])
        self.assertEqual(cleaned[1]["seed_memory_ids"], [])


if __name__ == "__main__":
    unittest.main()
