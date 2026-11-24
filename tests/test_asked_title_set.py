"""A title the person names in the message is the file's title, exactly as
written, whatever title the writer sent (fictional titles)."""

from __future__ import annotations

from pathlib import PurePosixPath
from types import SimpleNamespace
import unittest

from packages.doc_render.schema import Content
from services.orchestrator.deliverable_check import asked_title_gaps
from services.orchestrator.project_document_tools import asked_title_set

CASES = (
    (
        "Write a short report, title it 'Three Hives, One Meadow'.",
        "Three Hives, One Meadow",
    ),
    (
        "Fais un rapport intitulé « Le Verger d'Élise : bilan d'été ».",
        "Le Verger d'Élise : bilan d'été",
    ),
    (
        "Schreib einen Bericht mit dem Titel „Kälte, Wind und Wärme: ein Rückblick“.",
        "Kälte, Wind und Wärme: ein Rückblick",
    ),
    (
        "Haz un informe con el título «Tres colmenas, un prado: resumen».",
        "Tres colmenas, un prado: resumen",
    ),
    (
        "Scrivi una relazione con il titolo «L’orto di Nonna Lucia, anno uno».",
        "L’orto di Nonna Lucia, anno uno",
    ),
)


def content(title: str) -> Content:
    return Content.model_validate(
        dict(
            title=title,
            sections=[
                dict(heading=title, paragraphs=["The pilot ran for two seasons."]),
                dict(heading="Results", bullets=["Steady yield"]),
            ],
        )
    )


class AskedTitleSetTests(unittest.TestCase):
    def test_the_named_title_replaces_whatever_the_writer_sent(self) -> None:
        for request, title in CASES:
            stripped = "".join(c for c in title if c not in ",:'’").replace("  ", " ")
            for sent in (stripped, stripped.lower(), "Report"):
                with self.subTest(title=title, sent=sent):
                    tools = SimpleNamespace(request_text=request, built_titles={})
                    path = PurePosixPath("report.docx")
                    built = asked_title_set(tools, path, content(sent))
                    self.assertEqual(built.title, title)
                    self.assertEqual(built.sections[0].heading, title)
                    self.assertEqual(built.sections[1].heading, "Results")
                    self.assertEqual(tools.built_titles[str(path)], title)
                    self.assertEqual(asked_title_gaps(request, built), [])

    def test_a_message_without_a_named_title_changes_nothing(self) -> None:
        for request in (
            "Add a table of the weekly hours.",
            "Change the title of slide 3 to 'Costs'.",
            "Make it shorter, the title is fine.",
        ):
            with self.subTest(request=request):
                tools = SimpleNamespace(request_text=request, built_titles={})
                sent = content("Four Seasons Report")
                self.assertIs(
                    asked_title_set(tools, PurePosixPath("a.docx"), sent), sent
                )


if __name__ == "__main__":
    unittest.main()
