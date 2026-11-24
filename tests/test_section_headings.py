"""Section titles written as plain lines are headings in the file: real
heading styles in a Word file, heading-sized lines in a PDF. Reports are
fictional, in French, German and English."""

from __future__ import annotations

import io
from pathlib import Path
import tempfile
import unittest

from docx import Document
from pypdf import PdfReader

from packages.doc_render import render
from packages.doc_render.schema import Content
from services.orchestrator.project_document_input import (
    marked_titles,
    markdown,
    normalize,
)

REPORTS = {
    "fr": (
        "Bilan du potager partagé",
        [
            (
                "Introduction",
                "Le potager de la résidence compte douze parcelles entretenues par les voisins.",
            ),
            (
                "Récoltes de l'été",
                "Les tomates et les courgettes ont fourni la moitié des repas communs.",
            ),
            (
                "Calendrier d'automne",
                "| Tâche | Mois |\n| --- | --- |\n| Semis | Septembre |",
            ),
            (
                "Conclusion",
                "Le groupe souhaite agrandir le potager et ouvrir un atelier de compostage.",
            ),
        ],
    ),
    "de": (
        "Jahresbericht des Tauschregals",
        [
            (
                "Einleitung",
                "Das Tauschregal im Hausflur wird seit drei Jahren von den Nachbarn betreut.",
            ),
            (
                "Zahlen des Jahres",
                "- 412 Bücher getauscht\n- 38 neue Mitglieder\n- 6 Treffen",
            ),
            (
                "Ausblick",
                "Im nächsten Jahr soll ein zweites Regal im Innenhof aufgestellt werden.",
            ),
        ],
    ),
    "en": (
        "Harbour Clean-up Review",
        [
            (
                "Overview",
                "Volunteers gathered on six Saturdays to clear the old pier and the beach.",
            ),
            (
                "What was collected",
                "The teams removed nets, tyres and plastic, sorted by weight.",
            ),
            ("Costs", "| Item | Cost |\n| --- | --- |\n| Gloves | 40 |"),
            (
                "Next steps",
                "The group will repeat the clean-up in spring and invite the school.",
            ),
            (
                "Thanks",
                "The harbour office lent the boats and the scales free of charge.",
            ),
        ],
    ),
}


def plain_text(sections: list[tuple[str, str]]) -> str:
    return "\n\n".join(f"{name}\n\n{body}" for name, body in sections) + "\n"


def built(content: Content, suffix: str) -> bytes:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / ("report" + suffix)
        render(content, path, image_root=Path(directory))
        return path.read_bytes()


def line_sizes(raw: bytes) -> dict[str, float]:
    sizes: dict[str, float] = {}
    for page in PdfReader(io.BytesIO(raw)).pages:

        def visit(text: str, cm: object, tm: object, font: object, size: float) -> None:
            if text.strip():
                sizes[text.strip()] = max(sizes.get(text.strip(), 0.0), size)

        page.extract_text(visitor_text=visit)
    return sizes


class PlainTitlesTests(unittest.TestCase):
    def test_plain_title_lines_become_sections(self) -> None:
        for language, (title, sections) in REPORTS.items():
            with self.subTest(language=language):
                content = markdown(plain_text(sections), title)
                self.assertEqual(
                    [section.heading for section in content.sections[1:]]
                    if content.sections[0].heading == title
                    else [section.heading for section in content.sections],
                    [name for name, _ in sections],
                )

    def test_the_word_file_has_heading_styles(self) -> None:
        for language, (title, sections) in REPORTS.items():
            with self.subTest(language=language):
                value = normalize(
                    dict(path="report.docx", title=title, content=plain_text(sections))
                )
                content = Content.model_validate(value["content"])
                document = Document(io.BytesIO(built(content, ".docx")))
                headings = [
                    paragraph.text
                    for paragraph in document.paragraphs
                    if paragraph.style is not None
                    and paragraph.style.name.startswith("Heading")
                ]
                self.assertEqual(headings, [name for name, _ in sections])

    def test_the_pdf_draws_titles_larger_than_the_text(self) -> None:
        for language, (title, sections) in REPORTS.items():
            with self.subTest(language=language):
                value = normalize(
                    dict(path="report.pdf", title=title, content=plain_text(sections))
                )
                sizes = line_sizes(
                    built(Content.model_validate(value["content"]), ".pdf")
                )
                body = min(sizes.values())
                for name, _ in sections:
                    self.assertGreater(sizes[name], body + 2, name)


class LeftAsWrittenTests(unittest.TestCase):
    def test_a_text_with_heading_marks_is_not_read_again(self) -> None:
        lines = [
            "# Titre",
            "",
            "Texte d'ouverture assez long pour être un paragraphe.",
            "",
            "## Suite",
            "",
            "Autre texte assez long pour compter comme un paragraphe.",
        ]
        self.assertEqual(marked_titles(lines, "Titre"), lines)

    def test_short_lines_with_short_neighbours_are_no_titles(self) -> None:
        poem = "Lune\n\nRose\n\nSoir\n\nNuit\n".splitlines()
        self.assertEqual(marked_titles(poem, "Poème"), poem)

    def test_sentences_and_questions_are_no_titles(self) -> None:
        lines = (
            "Where did the money go?\n\nThe treasurer listed every purchase of the year in order.\n\n"
            "Thanks to everyone.\n\nThe committee met four times and kept minutes of every meeting.\n"
        ).splitlines()
        self.assertEqual(marked_titles(lines, "Notes"), lines)

    def test_one_candidate_alone_is_not_enough(self) -> None:
        lines = "Summary\n\nThe pond was cleaned in May by the whole street and the school.\n".splitlines()
        self.assertEqual(marked_titles(lines, "Pond"), lines)

    def test_the_title_line_itself_is_not_a_section(self) -> None:
        text = "Pond Diary\n\nThe pond was cleaned in May by the whole street.\n\nResults\n\nFive frogs and two newts came back to the pond by June.\n\nPlans\n\nThe street will plant reeds along the northern edge next year.\n"
        content = markdown(text, "Pond Diary")
        self.assertEqual(
            [section.heading for section in content.sections],
            ["Pond Diary", "Results", "Plans"],
        )

    def test_a_workbook_text_is_left_alone(self) -> None:
        text = "Rows\n\n| a | b |\n| --- | --- |\n| 1 | 2 |\n\nNotes\n\nSome longer note about the numbers above here.\n"
        grouped = markdown(text, "Sheet", grouped=False)
        self.assertEqual(len(grouped.sections), 1)


if __name__ == "__main__":
    unittest.main()
