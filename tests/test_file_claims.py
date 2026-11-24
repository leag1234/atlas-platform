"""What an answer says about the file it delivered is held to the file.

Fictional files only. The figures are counted on the built file
(packages/doc_render/figures.py); a sentence that gives a figure the file does
not have goes, with one code-written line of the measured figures instead.
"""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from packages.doc_render import render
from packages.doc_render.figures import measure
from packages.doc_render.schema import Content
from services.orchestrator import file_claims

FILES = {
    "sheet.docx": {
        "checkboxes": 4,
        "pages": 2,
        "words": 300,
        "bullets": 9,
        "pictures": 5,
    }
}


def built(content: dict[str, object], suffix: str) -> bytes:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / ("file" + suffix)
        render(Content.model_validate(content), path, image_root=Path(directory))
        return path.read_bytes()


class MeasureTests(unittest.TestCase):
    def test_figures_come_from_the_built_file(self) -> None:
        content = dict(
            title="Pond club",
            sections=[
                dict(heading="Tasks", bullets=["[ ] Feed fish", "[ ] Clean filter"]),
                dict(heading="Notes", paragraphs=["Bring boots."], bullets=["Gloves"]),
            ],
        )
        figures = measure(built(content, ".docx"), ".docx")
        self.assertEqual(figures["bullets"], 3)
        self.assertEqual(figures["checkboxes"], 2)
        self.assertEqual(figures["pictures"], 0)
        deck = measure(built(content, ".pptx"), ".pptx")
        from pptx import Presentation
        import io

        self.assertEqual(
            deck["slides"],
            len(Presentation(io.BytesIO(built(content, ".pptx"))).slides),
        )
        sheet = measure(
            built(
                dict(
                    title="Pond ledger",
                    sections=[
                        dict(
                            heading="Fish",
                            tables=[dict(headers=["Name"], rows=[["Carp"]])],
                        ),
                        dict(
                            heading="Plants",
                            tables=[dict(headers=["Name"], rows=[["Reed"]])],
                        ),
                    ],
                ),
                ".xlsx",
            ),
            ".xlsx",
        )
        self.assertEqual(sheet["sheets"], 2)

    def test_an_unreadable_file_gives_no_figure(self) -> None:
        self.assertEqual(measure(b"not a file", ".docx"), {})


class HeldToFileTests(unittest.TestCase):
    def check(self, language: str, text: str, kept: str, gone: str) -> str:
        answer, units = file_claims.held_to_file(text, FILES, language)
        self.assertIn(kept, answer)
        self.assertNotIn(gone, answer)
        self.assertTrue(units)
        return answer

    def test_a_missing_figure_is_removed_in_five_languages(self) -> None:
        cases = (
            (
                "en",
                "The handout has no checkboxes. Enjoy the garden.",
                "Enjoy",
                "no checkboxes",
                "4 boxes to tick",
            ),
            (
                "fr",
                "Le document ne contient aucune case à cocher. Bonne lecture.",
                "Bonne",
                "aucune",
                "4 cases à cocher",
            ),
            (
                "de",
                "Es gibt keine Kontrollkästchen. Viel Freude damit.",
                "Viel",
                "keine",
                "4 Kontrollkästchen",
            ),
            (
                "es",
                "El archivo no tiene ninguna casilla. Que lo disfrutes.",
                "Que lo",
                "ninguna",
                "4 casillas",
            ),
            (
                "it",
                "Il file non ha nessuna casella. Buon lavoro.",
                "Buon",
                "nessuna",
                "4 caselle",
            ),
        )
        for language, text, kept, gone, measured in cases:
            with self.subTest(language=language):
                answer = self.check(language, text, kept, gone)
                self.assertIn(measured, answer)

    def test_a_wrong_count_is_replaced_by_the_measured_one(self) -> None:
        cases = (
            ("en", "It has 12 bullets and 7 pictures.", "9 bullets", "5 pictures"),
            ("fr", "Il contient douze puces et sept images.", "9 puces", "5 images"),
            ("de", "Es enthält zwölf Aufzählungspunkte.", "9 Aufzählungspunkte", ""),
            ("es", "Tiene doce viñetas y siete imágenes.", "9 viñetas", "5 imágenes"),
            (
                "it",
                "Ha dodici punti elenco e sette immagini.",
                "9 punti elenco",
                "5 immagini",
            ),
        )
        for language, text, first, second in cases:
            with self.subTest(language=language):
                answer, _ = file_claims.held_to_file(text, FILES, language)
                self.assertIn(first, answer)
                self.assertIn(second, answer)
                self.assertNotIn("12", answer)

    def test_true_figures_limits_and_requests_stay(self) -> None:
        text = (
            "It has 9 bullets and 2 pages. It has at most 400 words. "
            "You asked for 5 slides. About 310 words in all."
        )
        self.assertEqual(file_claims.held_to_file(text, FILES, "en"), (text, []))

    def test_words_tolerate_rounding_but_not_a_wrong_count(self) -> None:
        self.assertEqual(
            file_claims.held_to_file("About 300 words.", FILES, "en")[1], []
        )
        self.assertEqual(
            file_claims.held_to_file("It has 500 words.", FILES, "en")[1], ["words"]
        )

    def test_all_on_one_page_over_a_workbook_of_sheets(self) -> None:
        sheets = {"plan.xlsx": {"sheets": 3, "words": 40}}
        answer, units = file_claims.held_to_file(
            "All the text is on one page.", sheets, "en"
        )
        self.assertIn("sheets", units)
        self.assertIn("3 sheets", answer)
        kept = "Each sheet fits on one page."
        self.assertEqual(file_claims.held_to_file(kept, sheets, "en"), (kept, []))

    def test_unmeasured_units_and_code_are_untouched(self) -> None:
        text = "It has 3 pages.\n\n```\nno checkboxes\n```"
        self.assertEqual(
            file_claims.held_to_file(text, {"a.pptx": {"slides": 4}}, "en"),
            (text, []),
        )
        self.assertEqual(
            file_claims.held_to_file("No boxes.", None, "en"), ("No boxes.", [])
        )


class StatusTests(unittest.TestCase):
    def test_tested_and_verified_claims_go_in_five_languages(self) -> None:
        for sentence in (
            "I tested it and everything works.",
            "J'ai testé le fichier, tout fonctionne.",
            "Ich habe die Datei getestet.",
            "Lo he probado y funciona.",
            "Ho testato il file.",
            "The file was verified.",
        ):
            with self.subTest(sentence=sentence):
                self.assertEqual(
                    file_claims.without_unproven_status(f"{sentence}\n\nDone here."),
                    "Done here.",
                )

    def test_advice_to_test_stays(self) -> None:
        text = "Try it with a small value first."
        self.assertEqual(file_claims.without_unproven_status(text), text)


if __name__ == "__main__":
    unittest.main()
