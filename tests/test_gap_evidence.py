"""A gap is told only on positive evidence, and an edit that makes room never
drops a list or recommendation item (staging -181: a Word table "missing Q3"
while its row was there; the table turn dropped "error rates" from a success
measure). The content is fictional."""

from __future__ import annotations

import json
import unittest

from services.orchestrator import current_file, deliverable_check

TEXT = (
    "Short Weeks at Kestrel Lane\nOrders\nQuarter | Orders | Sick days\n"
    "Q1 | 41 | 3\nQ2 | 47 | 2\nQ3 | 52 | 1\n"
)


class EvidenceTests(unittest.TestCase):
    def reply(self, *gaps: object) -> str:
        return json.dumps({"gaps": list(gaps), "variants": []})

    def test_a_missing_item_the_text_shows_is_no_gap(self) -> None:
        with self.assertLogs("services.orchestrator.deliverable_check", "WARNING"):
            found = deliverable_check.gaps_of(
                self.reply(
                    {"gap": "the table lacks Q3 data", "missing": "Q3 | 52 | 1"},
                    {"gap": "the table lacks Q4 data", "missing": "Q4"},
                    {"gap": "the title is wrong", "missing": ""},
                    "a plain gap",
                ),
                TEXT,
            )
        self.assertEqual(
            found,
            ["the table lacks Q4 data", "the title is wrong", "a plain gap"],
        )

    def test_without_the_text_every_gap_stays(self) -> None:
        found = deliverable_check.gaps_of(
            self.reply({"gap": "the table lacks Q3 data", "missing": "Q3"})
        )
        self.assertEqual(found, ["the table lacks Q3 data"])

    def test_the_rules_say_the_whole_text_and_keep_items(self) -> None:
        self.assertIn("the file's text is the whole file", deliverable_check.SYSTEM)
        self.assertIn('"missing"', deliverable_check.SYSTEM)
        self.assertIn("never drops an item", deliverable_check.REVISION)
        self.assertIn("never drop an item", current_file.ROOM)


if __name__ == "__main__":
    unittest.main()
