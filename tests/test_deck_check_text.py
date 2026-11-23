"""The checks of a deck read everything the deck shows, and never ask for the
person's own notes on its slides (staging -177: a slide of two key numbers
was read as empty, and "made from notes/projet.txt" was reported as a
missing visible source). The content is fictional."""

from __future__ import annotations

import unittest

from packages.doc_render.schema import Content
from services.orchestrator import deliverable_check, expectations

DECK = Content.model_validate(
    dict(
        title="Project Lapwing",
        sections=[
            dict(heading="Project Lapwing", bullets=["A bicycle library"]),
            dict(
                heading="Budget and team",
                metrics=[
                    dict(value="7 600 €", label="Approved budget"),
                    dict(value="2", label="People in the team"),
                ],
            ),
            dict(
                heading="Loans",
                chart=dict(
                    title="Loans per week",
                    labels=["June", "July"],
                    values=[40, 55.5],
                    unit="",
                ),
            ),
            dict(
                heading="Thank you",
                bullets=["Questions"],
                notes=["Lapwing: a bicycle library for 1 800 staff."],
            ),
        ],
    )
)


class DeckTextTests(unittest.TestCase):
    def test_key_numbers_charts_and_labelled_notes_are_read(self) -> None:
        text = deliverable_check.drawn_text(DECK)
        self.assertIn("7 600 € Approved budget", text)
        self.assertIn("2 People in the team", text)
        self.assertIn("Loans per week: June 40; July 55.5", text)
        self.assertIn(
            "(speaker notes, not on the slide) Lapwing: a bicycle library", text
        )

    def test_the_checks_never_ask_the_source_on_the_slides(self) -> None:
        self.assertIn("Where the file was made from", deliverable_check.SYSTEM)
        self.assertIn("A gap needs evidence", deliverable_check.SYSTEM)
        self.assertIn("A requirement fails only on evidence", expectations.JUDGE_SYSTEM)
        system = expectations.extraction_messages(
            expectations.Ledger("deck"), "from notes/lapwing.txt make 5 slides", []
        )[0]["content"]
        self.assertIn("not where the deliverable is made from", system)


if __name__ == "__main__":
    unittest.main()
