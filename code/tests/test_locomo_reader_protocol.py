import unittest

from code.locomo import convert_locomo_time_sliced
from code.pipeline import (
    generate_full_history_prompt_answer,
    memory_writer,
    raw_evidence_memories,
)
from code.temporal import canonicalize_relative_answer, resolve_temporal_mentions
from code.utils import locomo_answer_f1


class LocomoReaderProtocolTests(unittest.TestCase):
    def test_relative_dates_are_resolved_from_session_datetime(self):
        cases = {
            "I went yesterday": "7 May 2023",
            "We will go next month": "June 2023",
            "I spoke last week": "The week before 8 May 2023",
            "I ran last Saturday": "The Saturday before 8 May 2023",
        }
        for text, expected in cases.items():
            mentions = resolve_temporal_mentions(text, "1:56 pm on 8 May, 2023")
            self.assertEqual(mentions[0]["resolved"], expected)

    def test_conversion_preserves_dates_and_multimodal_metadata(self):
        records = [
            {
                "conversation": {
                    "session_1_date_time": "1:56 pm on 8 May, 2023",
                    "session_1": [
                        {
                            "dia_id": "D1:1",
                            "speaker": "A",
                            "text": "I painted it yesterday.",
                            "query": "painting sunrise",
                            "blip_caption": "a sunrise painting",
                            "img_url": ["https://example.test/image.jpg"],
                        }
                    ],
                },
                "qa": [
                    {
                        "question": "When was it painted?",
                        "answer": "7 May 2023",
                        "category": 2,
                        "evidence": ["D1:1"],
                    }
                ],
            }
        ]
        sample = convert_locomo_time_sliced(records)[0]
        turn = sample.history[0]
        self.assertEqual(turn.metadata["session_date_time"], "1:56 pm on 8 May, 2023")
        self.assertEqual(turn.metadata["image_query"], "painting sunrise")
        self.assertEqual(turn.metadata["temporal_mentions"][0]["resolved"], "7 May 2023")
        memory = memory_writer(sample.history)[0]
        self.assertEqual(memory.metadata["blip_caption"], "a sunrise painting")
        self.assertEqual(
            canonicalize_relative_answer("yesterday", [memory]),
            "7 May 2023",
        )
        raw = raw_evidence_memories(sample)[0]
        self.assertIn("image query: painting sunrise", raw.content)

    def test_official_metric_stems_tokens_and_handles_categories(self):
        self.assertEqual(locomo_answer_f1("adoption agency", "Adoption agencies", 1), 1.0)
        self.assertEqual(
            locomo_answer_f1("No information available.", "unanswerable", 5),
            1.0,
        )
        self.assertEqual(
            locomo_answer_f1("psychology", "psychology; unsupported rationale", 3),
            1.0,
        )

    def test_full_history_prompt_uses_timestamped_json_and_all_turns(self):
        sample = convert_locomo_time_sliced(
            [
                {
                    "conversation": {
                        "session_1_date_time": "1:56 pm on 8 May, 2023",
                        "session_1": [
                            {"dia_id": "D1:1", "speaker": "A", "text": "First fact."},
                            {"dia_id": "D1:2", "speaker": "B", "text": "Second fact."},
                        ],
                    },
                    "qa": [
                        {
                            "question": "What was second?",
                            "answer": "Second fact",
                            "category": 1,
                            "evidence": ["D1:2"],
                        }
                    ],
                }
            ]
        )[0]

        class FakeClient:
            def chat(self, messages, **_kwargs):
                prompt = messages[-1]["content"]
                self.prompt = prompt
                return "Second fact", {"prompt_tokens": 42, "completion_tokens": 2, "total_tokens": 44}

        client = FakeClient()
        result = generate_full_history_prompt_answer(sample, client, {})
        self.assertEqual(result["answer"], "Second fact")
        self.assertIn('"timestamp":"1:56 pm on 8 May, 2023"', client.prompt)
        self.assertIn("First fact.", client.prompt)
        self.assertIn("Second fact.", client.prompt)
        self.assertEqual(result["reader_trace"]["prompt_tokens"], 42)


if __name__ == "__main__":
    unittest.main()
