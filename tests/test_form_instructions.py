"""Form instructions of a delivered file: running footer, boxes to tick,
quoted texts written as quoted, slides that say more than their title.

Staging -184 (class suite): a footer asked on every page was written under
the body, quoted instructions lost their final period, a checklist with boxes
was printed with round bullets, and a how-to deck held only its titles.
Fictional content only.
"""

import tempfile
from typing import Any
import unittest
from pathlib import Path
from types import SimpleNamespace

from docx import Document
from pptx import Presentation
from pypdf import PdfReader

from packages.doc_render import Content, render
from packages.doc_render import readback
from packages.doc_render.cells import BOX, TICKED, boxed, checkbox, shown
from packages.doc_render.form import measure
from services.orchestrator import deliverable_check
from services.orchestrator.project_document_input import markdown

GUIDE = """# Pottery evening

## Wedging the clay

- Cut the block in two.
- Press out every air bubble.

\\newpage

## Before you leave

Rinse your tools in the grey basin.

### To check

- [ ] The wheel is switched off.
- [x] The kiln log is signed.

\\footer Thursday studio, room B
"""


class RunningFooterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.content = markdown(GUIDE, "Pottery evening")

    def build(self, suffix: str) -> bytes:
        path = self.root / ("guide" + suffix)
        render(self.content, path, image_root=self.root)
        return path.read_bytes()

    def test_markdown_footer_line_is_the_footer_not_body_text(self) -> None:
        self.assertEqual(self.content.footer, "Thursday studio, room B")
        self.assertNotIn("Thursday studio, room B", self.content.texts())
        alone = markdown("Intro line.\n\n\\footer: Kassel Werkstatt\n", "Notiz")
        self.assertEqual(alone.footer, "Kassel Werkstatt")
        self.assertIsNone(markdown("Just a line.", "Note").footer)

    def test_pdf_prints_the_footer_at_the_foot_of_every_page(self) -> None:
        raw = self.build(".pdf")
        reader = PdfReader(self.root / "guide.pdf")
        self.assertEqual(len(reader.pages), 2)
        for page in reader.pages:
            self.assertIn("Thursday studio, room B", page.extract_text())
        # Read back apart from the body, and never measured as body text.
        back = readback.read(raw, ".pdf")
        self.assertEqual(back.footer, "Thursday studio, room B")
        for section in back.sections:
            self.assertNotIn("Thursday studio, room B", section.paragraphs)
        for printed in measure(raw).pages:
            self.assertFalse(any("Thursday" in line.text for line in printed.lines))
        self.assertIn(
            "\\footer Thursday studio, room B", readback.editable(back)["content"]
        )

    def test_docx_writes_a_page_footer(self) -> None:
        raw = self.build(".docx")
        document = Document(str(self.root / "guide.docx"))
        footer = document.sections[0].footer
        self.assertFalse(footer.is_linked_to_previous)
        self.assertEqual(footer.paragraphs[0].text, "Thursday studio, room B")
        self.assertNotIn(
            "Thursday studio, room B", [p.text for p in document.paragraphs]
        )
        self.assertEqual(readback.read(raw, ".docx").footer, "Thursday studio, room B")

    def test_deck_footer_line_holds_the_running_footer(self) -> None:
        raw = self.build(".pptx")
        deck = Presentation(str(self.root / "guide.pptx"))
        footers = {
            shape.text_frame.text
            for slide in deck.slides
            for shape in slide.shapes
            if shape.name == "atlas:footer"
        }
        self.assertEqual(footers, {"Thursday studio, room B"})
        self.assertEqual(readback.read(raw, ".pptx").footer, "Thursday studio, room B")

    def test_without_footer_nothing_changes(self) -> None:
        content = markdown("## Glazes\n\nDip twice.\n", "Glaze notes")
        path = self.root / "plain.docx"
        render(content, path, image_root=self.root)
        self.assertTrue(Document(str(path)).sections[0].footer.is_linked_to_previous)
        self.assertEqual(readback.read(path.read_bytes(), ".docx").footer, "")

    def test_check_reads_the_footer_as_a_footer(self) -> None:
        text = deliverable_check.drawn_text(self.content)
        self.assertIn("(page footer, at the foot of every page) Thursday studio", text)


class BoxToTickTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.content = markdown(GUIDE, "Pottery evening")

    def test_markers_read_as_boxes(self) -> None:
        self.assertEqual(boxed("[ ] Lock the door."), BOX + " Lock the door.")
        self.assertEqual(boxed("[x] Done."), TICKED + " Done.")
        self.assertEqual(
            boxed("[link](https://example.org) here"),
            "[link](https://example.org) here",
        )
        self.assertEqual(shown("[ ] Fenster schließen."), BOX + " Fenster schließen.")
        self.assertEqual(
            checkbox(BOX + " Cerrar la puerta."), (BOX, "Cerrar la puerta.")
        )
        self.assertEqual(checkbox("Plain point."), ("", "Plain point."))

    def test_pdf_draws_boxes_not_bullets(self) -> None:
        path = self.root / "guide.pdf"
        render(self.content, path, image_root=self.root)
        text = PdfReader(path).pages[1].extract_text()
        self.assertIn("The wheel is switched off.", text)
        self.assertNotIn("[ ]", text)

    def test_docx_box_replaces_the_bullet_and_reads_back(self) -> None:
        path = self.root / "guide.docx"
        render(self.content, path, image_root=self.root)
        document = Document(str(path))
        boxes = [p for p in document.paragraphs if p.text.startswith((BOX, TICKED))]
        self.assertEqual(len(boxes), 2)
        for paragraph in boxes:
            properties: Any = paragraph._p.pPr
            self.assertEqual(properties.numPr.numId.val, 0)
        plain = [p for p in document.paragraphs if p.text == "Cut the block in two."]
        properties = plain[0]._p.pPr
        self.assertIsNone(None if properties is None else properties.numPr)
        bullets = readback.read(path.read_bytes(), ".docx").sections[-1].bullets
        self.assertEqual(
            bullets,
            [BOX + " The wheel is switched off.", TICKED + " The kiln log is signed."],
        )

    def test_deck_box_has_no_bullet_and_reads_back_as_a_point(self) -> None:
        content = markdown(
            "## Avant la séance\n\n- [ ] Couvrir le tour.\n- [ ] Ranger les estèques.\n- Vider le seau.\n",
            "Atelier",
        )
        path = self.root / "deck.pptx"
        render(content, path, image_root=self.root)
        back = readback.read(path.read_bytes(), ".pptx")
        points = [point for section in back.sections for point in section.bullets]
        self.assertIn(BOX + " Couvrir le tour.", points)
        self.assertIn("Vider le seau.", points)


def tools(request: str, *earlier: str) -> SimpleNamespace:
    return SimpleNamespace(request_text=request, earlier_requests=list(earlier))


class QuotedTextTests(unittest.TestCase):
    def test_final_period_dropped_is_a_gap(self) -> None:
        content = markdown(
            "## Sheet one\n\n- Fold the corner twice\n- Glue the flap\n", "Card"
        )
        found = deliverable_check.quote_gaps(
            tools("Write the two steps: “Fold the corner twice.” and “Glue the flap.”"),
            content,
            b"",
        )
        self.assertEqual(len(found), 1)
        self.assertIn("«Fold the corner twice.»", found[0])
        self.assertIn("«Glue the flap.»", found[0])

    def test_quote_merged_with_a_small_letter_is_a_gap(self) -> None:
        content = markdown(
            "## Consignes\n\nPour finir, rangez les pinceaux dans le pot bleu.\n",
            "Fiche",
        )
        found = deliverable_check.quote_gaps(
            tools("Écrivez : « Rangez les pinceaux dans le pot bleu. »"), content, b""
        )
        self.assertEqual(len(found), 1)

    def test_quotes_written_as_typed_or_absent_are_no_gap(self) -> None:
        content = markdown(
            "## Ablauf\n\n- Den Leim dünn auftragen.\n- Zehn Minuten pressen.\n",
            "Leimkarte",
        )
        request = tools(
            "Bitte „Den Leim dünn auftragen.“ und „Zehn Minuten pressen.“ übernehmen; "
            "das Wort „Klebepunkte“ muss nicht auf die Karte, und „Schritt drei fehlt“ "
            "war nur eine Bemerkung.",
        )
        self.assertEqual(deliverable_check.quote_gaps(request, content, b""), [])

    def test_earlier_requests_count_and_typography_is_not_a_change(self) -> None:
        content = markdown("## Note\n\nDon’t lean on the shelf.\n", "Note")
        request = tools("Make it a PDF.", 'Write "Don\'t lean on the shelf."')
        self.assertEqual(deliverable_check.quote_gaps(request, content, b""), [])

    def test_text_file_is_read_from_its_preview(self) -> None:
        found = deliverable_check.quote_gaps(
            tools("Put « Merci de fermer la grille. » at the end."),
            None,
            "Horaires\nmerci de fermer la grille\n".encode(),
        )
        self.assertEqual(len(found), 1)


class RestatedSlideTests(unittest.TestCase):
    def deck(self, text: str, title: str) -> Content:
        return markdown(text, title)

    def test_slides_that_only_say_their_title_are_a_gap(self) -> None:
        content = self.deck(
            "## Lantern parade\n\nOur route this year.\n\n"
            "## Folding\n\nFold the lantern paper.\n\n"
            "## Lighting\n\nLighting the lanterns.\n",
            "Lantern paper",
        )
        found = deliverable_check.restated_gaps(".pptx", content, "lantern-paper.pptx")
        self.assertEqual(len(found), 1)
        self.assertIn("Folding", found[0])
        self.assertIn("Lighting", found[0])

    def test_slides_with_content_and_the_cover_are_no_gap(self) -> None:
        content = self.deck(
            "## Kürbisfest\n\nKürbisfest im Hof.\n\n"
            "## Schnitzen\n\n- Deckel schräg abschneiden, damit er nicht hineinfällt.\n"
            "- Kerne mit einem Esslöffel auskratzen.\n\n"
            "## Beleuchtung\n\nTeelichter erst nach dem Aufstellen anzünden und nie unbeaufsichtigt lassen.\n",
            "Kürbisfest",
        )
        self.assertEqual(deliverable_check.restated_gaps(".pptx", content), [])

    def test_report_section_is_measured_too(self) -> None:
        content = self.deck(
            "## Inventaire\n\nLa liste des pots.\n\n## Rangement\n\nRangement.\n",
            "Atelier",
        )
        found = deliverable_check.restated_gaps(".docx", content, "atelier.docx")
        self.assertEqual(len(found), 1)
        self.assertIn("Rangement", found[0])
        self.assertNotIn("Inventaire", found[0])


if __name__ == "__main__":
    unittest.main()
