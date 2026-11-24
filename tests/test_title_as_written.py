"""A title the person or the writer gives is kept as written, punctuation
included; a file name may be a slug and never comes back as the title.

Fictional titles in several languages, with commas, colons, apostrophes and
accents."""

from __future__ import annotations

from types import SimpleNamespace
import unittest

from packages.doc_render.schema import Content
from services.orchestrator.deliverable_check import asked_title_gaps
from services.orchestrator.project_document_input import normalize

TITLES = (
    ("Three Hives, One Meadow", "Three_Hives_One_Meadow"),
    ("Le Verger d'Élise : bilan d'été", "verger-elise-bilan"),
    ("Kälte, Wind und Wärme: ein Rückblick", "Kaelte_Wind_Waerme"),
    ("Tres colmenas, un prado: resumen", "tres_colmenas"),
    ("L'orto di Nonna Lucia, anno uno", "orto_nonna_lucia"),
)
BODY = (
    "{title}\n\n"
    "The pilot ran for two seasons with a small group of volunteers and notes.\n\n"
    "Context\n\n"
    "Every visit was logged with the weather, the time spent and what changed.\n\n"
    "Results\n\n"
    "- Steady yield\n- Fewer missed visits\n"
)


class TitleAsWrittenTests(unittest.TestCase):
    def built(self, title: str, stem: str, given: str | None) -> Content:
        arguments = dict(
            path=f"{stem}.docx", content=BODY.format(title=title), strategy="created"
        )
        if given is not None:
            arguments["title"] = given
        return Content.model_validate(normalize(arguments)["content"])

    def test_opening_line_is_the_title_without_a_title_argument(self) -> None:
        for title, stem in TITLES:
            with self.subTest(title=title):
                content = self.built(title, stem, None)
                self.assertEqual(content.title, title)
                self.assertEqual(content.sections[0].heading, title)
                self.assertTrue(content.repeats_title())
                self.assertNotIn(
                    title, [t for x in content.sections for t in x.paragraphs]
                )

    def test_a_given_title_is_written_once(self) -> None:
        for title, stem in TITLES:
            with self.subTest(title=title):
                content = self.built(title, stem, title)
                self.assertEqual(content.title, title)
                self.assertTrue(content.repeats_title())
                self.assertNotIn(
                    title, [t for x in content.sections for t in x.paragraphs]
                )

    def test_a_file_name_given_as_title_never_replaces_the_written_one(self) -> None:
        for title, stem in TITLES:
            with self.subTest(title=title):
                self.assertEqual(self.built(title, stem, stem).title, title)

    def test_a_text_without_a_title_line_keeps_its_first_paragraph(self) -> None:
        text = "The pilot ran for two seasons with a small group of volunteers.\n"
        content = normalize(
            dict(path="pilot_notes.docx", content=text, strategy="created")
        )["content"]
        self.assertEqual(content["title"], "Pilot notes")
        self.assertIn(text.strip(), content["sections"][0]["paragraphs"])


class AskedTitleTests(unittest.TestCase):
    def test_lost_punctuation_is_a_gap(self) -> None:
        for title, _ in TITLES:
            with self.subTest(title=title):
                request = f"Write a short report, title it “{title}”."
                lost = "".join(c for c in title if c not in ",:'")
                self.assertTrue(asked_title_gaps(request, SimpleNamespace(title=lost)))
                self.assertEqual(
                    asked_title_gaps(request, SimpleNamespace(title=title)), []
                )
                typographic = title.replace("'", "’")
                self.assertEqual(
                    asked_title_gaps(request, SimpleNamespace(title=typographic)), []
                )


if __name__ == "__main__":
    unittest.main()
