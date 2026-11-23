"""The size a person asks for a file is the size of the file (staging -151).

Three defects of the battery of 2026-11-21, one class: a part code adds, a
page break or a claim makes the file differ from the size asked.

1. A deck asked with five slides came with six: a Sources slide repeated the
   person's own notes. The excerpts of the person's files now go in the
   speaker notes of the last slide; a document gets no Sources section.
2. A report asked "about one page" printed on two pages and its size
   requirement was counted as met (about 1 allowed 2); the answer called it a
   one-page report. Over the size, pages keep 20 % only, and the closing
   states the measured length.
3. "Recommendations" and its lead-in "we recommend:" ended page 1 and the
   points were on page 2: a lead-in keeps with its list, a short list stays
   together, in the DOCX (keep with next) and the PDF layout alike; the form
   check finds a lead-in at a page foot.

The content is fictional.
"""

from __future__ import annotations

import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
from typing import Any
import unittest

from packages.doc_render import form, render
from packages.doc_render.deck import kind
from packages.doc_render.layout import (
    A4,
    SHORT_LIST,
    kept_with_next,
    lead_in,
    pages,
)
from packages.doc_render.schema import Content
from services.orchestrator import expectations, work_closing
from services.orchestrator.presentation_delivery import build_project_document
from services.orchestrator.project_document_input import normalize
from services.orchestrator.project_document_tools import (
    include_sources,
    measured_length,
)

# The person's notes, read as the lines of a text file.
NOTES = [
    "Project Lapwing: a bicycle library for the staff of a hospital of 1 800 people.",
    "Goal: lend 120 bicycles a week by the end of 2027.",
    "Budget approved: 7 600 euros. Team: 2 people. Lead: Mira Oakhurst.",
    "Calendar: needs survey in February 2026, workshop in April, pilot in June,",
    "review in October. Risks: theft, repairs.",
]
PAGE = "https://example.org/bicycle-libraries"
SLIDES: list[dict[str, Any]] = [
    {
        "title": "Project Lapwing",
        "bullets": ["A bicycle library for the hospital staff", "Lead: Mira Oakhurst"],
    },
    {
        "title": "Why bicycles",
        "bullets": [
            "Shared bicycles cut short car trips in towns of this size "
            f"(source: {PAGE})",
            "Goal: 120 loans a week by the end of 2027",
        ],
    },
    {
        "title": "Means",
        "bullets": ["Budget approved: 7 600 euros", "Team of 2 people"],
    },
    {
        "title": "Calendar",
        "bullets": [
            "February 2026: needs survey",
            "April 2026: workshop",
            "June 2026: pilot",
            "October 2026: review",
        ],
        "sequence": True,
    },
    {"title": "Thank you", "bullets": ["Questions from the board"]},
]
REPORT = f"""## Introduction

Project Lapwing lends bicycles to the staff of a hospital of 1 800 people.
Towns of this size see shared bicycles replace short car trips ({PAGE}).

## Calendar

| Step | Month |
| --- | --- |
| Needs survey | February 2026 |
| Workshop | April 2026 |
| Pilot | June 2026 |
| Review | October 2026 |

## Conclusion

With a budget of 7 600 euros and a team of two, the library starts in June.
"""
FILLER = "The trial kept every team at its usual output across the quarter."


def content_of(arguments: dict[str, Any], path: str) -> Content:
    value = normalize(dict(arguments, path=path, sources=[]))
    return Content.model_validate(value["content"])


def built(content: Content, suffix: str, sources: list[str]) -> bytes:
    return build_project_document(
        content, suffix, sources, max_bytes=16 * 1024 * 1024, timeout=60
    ).content


def requirement(unit: str, count: int, relation: str) -> expectations.Requirement:
    return expectations.Requirement(
        id="r1",
        statement=f"{relation} {count} {unit}",
        turn=1,
        method="code",
        check={"part": "size", "unit": unit, "count": count, "relation": relation},
    )


class DeckSizeTests(unittest.TestCase):
    def test_five_slides_asked_with_notes_and_a_page_read_stay_five(self) -> None:
        deck = content_of(dict(title="Project Lapwing", slides=SLIDES), "lapwing.pptx")
        noted = include_sources(deck, NOTES, deck=True)
        self.assertEqual(len(noted.sections), 5)
        self.assertEqual(
            [s.heading for s in noted.sections], [s["title"] for s in SLIDES]
        )
        self.assertEqual(kind(noted.sections[-1], 4, 5), "closing")
        raw = built(noted, ".pptx", NOTES)
        from pptx import Presentation

        shown_deck = Presentation(io.BytesIO(raw))
        self.assertEqual(len(shown_deck.slides), 5)
        # The page read in the conversation stays cited on its slide.
        texts = " ".join(
            shape.text_frame.text
            for shape in shown_deck.slides[1].shapes
            if shape.has_text_frame
        )
        self.assertIn(PAGE, texts)
        notes = " ".join(
            shown_deck.slides[-1].notes_slide.notes_text_frame.text.split()
        )
        for line in NOTES:
            self.assertIn(line, notes)
        tools = SimpleNamespace(file_measures=None)
        self.assertEqual(measured_length(tools, ".pptx", raw), {"slides": 5})
        # The ledger's measure wins when it has one.
        tools.file_measures = {"slides": 5, "pages": None, "words": 80}
        self.assertEqual(measured_length(tools, ".pptx", raw), {"slides": 5})
        item = requirement("slides", 5, "exact")
        self.assertTrue(expectations.size_result(item, noted, "", {"slides": 5}).passed)

    def test_a_deck_from_the_notes_only_gets_no_slide_for_them(self) -> None:
        slides = [dict(slide) for slide in SLIDES[:-1]]
        slides[1] = {"title": "Why bicycles", "bullets": ["Goal: 120 loans a week"]}
        deck = content_of(dict(title="Project Lapwing", slides=slides), "lapwing.pptx")
        noted = include_sources(deck, NOTES, deck=True)
        self.assertEqual(len(noted.sections), 4)
        self.assertEqual(noted.sections[-1].bullets, deck.sections[-1].bullets)
        self.assertNotIn("Sources", [s.heading for s in noted.sections])
        self.assertEqual(include_sources(noted, NOTES, deck=True), noted)


class ReportSizeTests(unittest.TestCase):
    def test_a_one_page_report_with_sources_stays_on_one_page(self) -> None:
        report = content_of(dict(title="Project Lapwing", content=REPORT), "r.docx")
        self.assertEqual(include_sources(report, NOTES), report)
        raw = built(report, ".docx", [])
        from docx import Document

        paragraphs = [p.text for p in Document(io.BytesIO(raw)).paragraphs]
        self.assertNotIn("Sources", paragraphs)
        self.assertFalse(any(NOTES[0] in text for text in paragraphs))
        self.assertTrue(any(PAGE in text for text in paragraphs))
        pdf = built(report, ".pdf", [])
        self.assertEqual(form.measure(pdf).count, 1)

    def test_about_one_page_on_two_pages_is_not_met(self) -> None:
        item = requirement("pages", 1, "about")
        report = content_of(dict(title="Project Lapwing", content=REPORT), "r.docx")
        over = expectations.size_result(item, report, "", {"pages": 2})
        self.assertFalse(over.passed)
        self.assertIn("the file has 2 pages, about 1 (from 0 to 1)", over.how)
        self.assertTrue(expectations.size_result(item, report, "", {"pages": 1}).passed)
        two = requirement("pages", 2, "about")
        self.assertFalse(expectations.size_result(two, report, "", {"pages": 3}).passed)
        self.assertTrue(expectations.size_result(two, report, "", {"pages": 1}).passed)
        five = requirement("pages", 5, "about")
        self.assertTrue(expectations.size_result(five, report, "", {"pages": 6}).passed)
        self.assertFalse(
            expectations.size_result(five, report, "", {"pages": 7}).passed
        )
        # Words keep their margin either way.
        words = requirement("words", 300, "about")
        self.assertTrue(
            expectations.size_result(words, report, "", {"words": 360}).passed
        )


def recommendations(filler: int, points: int = 4) -> Content:
    return Content.model_validate(
        dict(
            title="Four-day week",
            sections=[
                dict(
                    heading="Findings",
                    # Paragraphs of at most 50 sentences (4000 characters).
                    paragraphs=[
                        " ".join([FILLER] * min(50, filler - start))
                        for start in range(0, filler, 50)
                    ],
                ),
                dict(
                    heading="Recommendations",
                    paragraphs=["Based on the trial, we recommend:"],
                    bullets=[f"Point {n} of the plan" for n in range(points)],
                ),
            ],
        )
    )


def page_of(laid: Any, text: str) -> int:
    for number, page in enumerate(laid):
        for placed in page:
            if any(text in line for lines in placed.block.lines for line in lines):
                return number
    raise AssertionError(text)


class KeepTogetherTests(unittest.TestCase):
    def test_the_rule_marks_lead_ins_and_short_lists(self) -> None:
        section = recommendations(1).sections[1]
        self.assertTrue(lead_in("we recommend:"))
        self.assertTrue(lead_in("Wir empfehlen\uff1a"))
        self.assertFalse(lead_in("We recommend this."))
        self.assertEqual(kept_with_next(section), (True, True, True, True, False))
        long = recommendations(1, SHORT_LIST + 3).sections[1]
        flags = kept_with_next(long)
        # The lead-in, the first point with the second, the last two together.
        self.assertEqual(flags[:3], (True, True, False))
        self.assertEqual(flags[-3:], (False, True, False))
        # A lead-in with nothing after it in its section keeps nothing.
        alone = Content.model_validate(
            dict(title="T", sections=[dict(heading="H", paragraphs=["Note:"])])
        ).sections[0]
        self.assertEqual(kept_with_next(alone), (False,))

    def test_the_docx_keeps_the_lead_in_with_its_list(self) -> None:
        content = recommendations(3)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "r.docx"
            render(content, path, image_root=Path(directory))
            from docx import Document

            document = Document(str(path))
        kept = {
            p.text: bool(p.paragraph_format.keep_with_next) for p in document.paragraphs
        }
        self.assertTrue(kept["Recommendations"])
        self.assertTrue(kept["Based on the trial, we recommend:"])
        self.assertTrue(kept["Point 0 of the plan"])
        self.assertTrue(kept["Point 2 of the plan"])
        self.assertFalse(kept["Point 3 of the plan"])
        self.assertFalse(kept[" ".join([FILLER] * 3)])

    def test_heading_lead_in_and_list_never_part_at_a_page_boundary(self) -> None:
        moved = 0
        for filler in range(40, 80):
            with self.subTest(filler=filler):
                laid = pages(recommendations(filler), Path("."), geometry=A4)
                heading = page_of(laid, "Recommendations")
                self.assertEqual(heading, page_of(laid, "we recommend:"))
                self.assertEqual(heading, page_of(laid, "Point 0"))
                self.assertEqual(heading, page_of(laid, "Point 3"))
                moved += heading > page_of(laid, "usual output")
        # Some of these lengths put the section at a page foot.
        self.assertGreater(moved, 0)

    def test_a_lead_in_at_a_page_foot_is_found(self) -> None:
        from reportlab.pdfgen.canvas import Canvas

        output = io.BytesIO()
        canvas = Canvas(output, pagesize=(595, 842))
        for page in range(2):
            canvas.setFont("Helvetica", 12)
            for row in range(50):
                canvas.drawString(50, 790 - row * 14, "A line of body text.")
            if page == 0:
                canvas.drawString(50, 70, "Based on the trial, we recommend:")
            canvas.showPage()
        canvas.save()
        found = form.findings(form.measure(output.getvalue()))
        self.assertEqual(len(found), 1)
        self.assertIn(
            'ends with the lead-in "Based on the trial, we recommend:"', found[0]
        )


class LengthClaimTests(unittest.TestCase):
    def test_the_closing_states_the_measured_length(self) -> None:
        receipt = {"relative_path": "friday.docx"}
        text = work_closing.closing_instruction(
            [receipt], (), True, [("friday.docx", {"pages": 2})]
        )
        self.assertIn("Measured on the built file, friday.docx has 2 pages.", text)
        self.assertIn("never say it fits on fewer pages or slides", text)
        one = work_closing.lengths_text([("deck.pptx", {"slides": 1})])
        self.assertEqual(one, "deck.pptx has 1 slide")
        # Nothing measured, nothing said.
        plain = work_closing.closing_instruction([receipt], (), True, [])
        self.assertNotIn("Measured on the built file", plain)

    def test_the_closing_step_reads_the_length_of_the_published_file(self) -> None:
        output = {
            "deliverable": {"relative_path": "friday.docx"},
            "length": {"pages": 1},
            "verification": {"status": "passed"},
        }
        tools = SimpleNamespace(selection=None)
        text = work_closing.closing_step(tools, True, [("publish_document", output)])
        assert text is not None
        self.assertIn("friday.docx has 1 page.", text)


if __name__ == "__main__":
    unittest.main()
