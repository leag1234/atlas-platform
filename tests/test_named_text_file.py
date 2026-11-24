"""A file named with its extension is delivered in that format: the name
decides the suffix of the file published first. Names are fictional."""

from __future__ import annotations

import unittest

from packages.text_deliverables import named_text_suffix
from services.orchestrator.code_mode import requested_suffix
from services.orchestrator.request_router import Decision


class NamedTextSuffixTests(unittest.TestCase):
    def test_one_named_text_file_decides_the_format(self) -> None:
        for text, suffix in (
            ("Can you deliver an SVG file called `otter-badge.svg`?", ".svg"),
            ("Livre-moi `carte-jardin.SVG` avec une feuille bleue", ".svg"),
            ("Gib mir bitte stundenplan.csv mit fünf Zeilen", ".csv"),
            ("Necesito la página bienvenida.html para el club", ".html"),
            ("write the rota into rota-notes.txt", ".txt"),
        ):
            with self.subTest(text=text):
                self.assertEqual(named_text_suffix(text), suffix)

    def test_no_name_or_several_formats_decide_nothing(self) -> None:
        for text in (
            "make me a CV for a baker",
            "turn rota.csv into rota.md for the notice board",
            "dessine un hérisson",
            "see example.org for the details",
        ):
            with self.subTest(text=text):
                self.assertIsNone(named_text_suffix(text))

    def test_an_office_format_name_is_not_a_text_format(self) -> None:
        self.assertIsNone(named_text_suffix("fill budget.xlsx with the totals"))


class RequestedSuffixTests(unittest.TestCase):
    def test_a_routed_file_turn_takes_the_name_of_its_file(self) -> None:
        routed = Decision.model_validate({"kind": "file", "file_format": "md"})
        self.assertEqual(
            requested_suffix("Deliver a vector file named fox-crest.svg", routed),
            ".svg",
        )

    def test_an_office_format_wins_and_other_kinds_are_free(self) -> None:
        office = Decision.model_validate({"kind": "file", "file_format": "docx"})
        self.assertEqual(requested_suffix("A Word note in notes.txt", office), ".docx")
        answer = Decision.model_validate({"kind": "run"})
        self.assertIsNone(requested_suffix("run check.py and show notes.txt", answer))

    def test_without_a_routed_decision_the_words_decide(self) -> None:
        self.assertEqual(
            requested_suffix("Please make me a file called rota.csv", None), ".csv"
        )
        self.assertIsNone(requested_suffix("What is in rota.csv?", None))


if __name__ == "__main__":
    unittest.main()
