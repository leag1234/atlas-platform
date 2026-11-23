"""A deck from a project note (staging -145, 2026-11-21): three defect classes.

1. The cover and every footer showed the file slug "presentation-hirondelle":
   the outline gave no title and the file stem stood in for it. No
   deliverable (deck, document, workbook, PDF) is titled by a file name.
2. Two Sources slides, the last one drawn as the dark closing slide. Since
   staging -151 the excerpts of the source are on no slide of their own: they
   go in the speaker notes of the last slide (a deck asked with five slides
   had six).
3. A word cut inside a narrow card ("communicatio" / "n engageante"): the
   renderer keeps words whole, and the cut is detected on the rendered text.
4. A number cut at its thousands separator ("campus de 3" / "000 personnes").

The content is fictional.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
import unittest

from pptx import Presentation

from packages.doc_render import readback, render
from packages.doc_render.deck import kind, quoted
from packages.doc_render.schema import Content
from packages.doc_render.layout import font, wrap
from packages.doc_render.spacing import bound, words
from packages.doc_render.titles import natural, slug_like
from packages.doc_render.verify import VerificationError
from packages.doc_render.wordbreaks import overflowing, split_words
from services.orchestrator.deliverable_check import title_gaps
from services.orchestrator.presentation_delivery import build_project_document
from services.orchestrator.project_document_input import normalize
from services.orchestrator.project_document_tools import include_sources

SLIDES: list[dict[str, Any]] = [
    {
        "title": "Kestrel Project",
        "bullets": [
            "A shared shuttle for the staff of a campus of 2 400 people",
            "Goal: halve single-occupancy car trips by the end of 2027",
        ],
    },
    {
        "title": "Calendar",
        "bullets": [
            "January 2026: needs study",
            "March 2026: prototype",
            "May 2026: pilot with 150 staff",
        ],
    },
    {
        "title": "Identified risks",
        "bullets": [
            "Weak adoption by the staff",
            "Protection of location data",
            "Need for an engaging communication",
            "Compliance with internationalisation rules",
        ],
    },
    {"title": "Thank you", "bullets": ["Questions from the steering board"]},
]
EXCERPTS = [
    "Kestrel Project: a shared shuttle for the staff of a campus of 2 400 people.",
    "Goal: halve single-occupancy car trips by the end of 2027.",
    "Budget: 9 800 euros. Team: 3 people. Lead: Ada Fernhill.",
    "Calendar: needs study in January 2026, prototype in March, pilot in May,",
    "review in September. Risks: weak adoption, protection of location data.",
    "Indicators: shared trips per week, occupancy, satisfaction.",
]


def content_of(arguments: dict[str, Any]) -> Content:
    return Content.model_validate(normalize(dict(arguments, sources=[]))["content"])


def built(
    content: Content, suffix: str = ".pptx", sources: tuple[str, ...] | list[str] = ()
) -> bytes:
    return build_project_document(
        content,
        suffix,
        list(sources),
        max_bytes=16 * 1024 * 1024,
        timeout=60,
        pictures={},
    ).content


def deck_of(raw: bytes) -> Any:
    import io

    return Presentation(io.BytesIO(raw))


def heading(slide: Any) -> str:
    return next(
        (
            " ".join(shape.text_frame.text.split())
            for shape in slide.shapes
            if shape.name == "atlas:title"
        ),
        "",
    )


class TitleTests(unittest.TestCase):
    def test_a_slug_is_told_from_a_title(self) -> None:
        for slug in (
            "presentation-hirondelle",
            "rapport_annuel",
            "Rapport_Annuel",
            "plan.pptx",
            "budget-2026",
        ):
            with self.subTest(slug=slug):
                self.assertTrue(slug_like(slug))
        self.assertTrue(slug_like("budget", "budget"))
        for title in (
            "Projet Hirondelle",
            "E-commerce",
            "e-commerce",
            "Wi-Fi",
            "COVID-19",
            "Budget",
            "Kestrel",
        ):
            with self.subTest(title=title):
                self.assertFalse(slug_like(title, "budget"))
        self.assertEqual(natural("rapport_annuel"), "Rapport annuel")
        self.assertEqual(
            natural("presentation-kestrel", "", ("Kestrel Project",)),
            "Kestrel Project",
        )

    def test_a_deck_without_a_title_takes_its_cover_heading(self) -> None:
        for title in (None, "presentation-kestrel"):
            with self.subTest(title=title):
                arguments: dict[str, Any] = dict(
                    path="presentation-kestrel.pptx", slides=SLIDES
                )
                if title:
                    arguments["title"] = title
                content = content_of(arguments)
                self.assertEqual(content.title, "Kestrel Project")
                deck = deck_of(built(content))
                self.assertEqual(deck.core_properties.title, "Kestrel Project")
                texts = " ".join(
                    shape.text_frame.text
                    for slide in deck.slides
                    for shape in slide.shapes
                    if shape.has_text_frame
                )
                self.assertNotIn("presentation-kestrel", texts)

    def test_documents_and_workbooks_never_show_the_file_name(self) -> None:
        cases = {
            ".docx": "Ridership grew in the first month.\n\n## Next\n\nOpen a second line.",
            ".pdf": "Ridership grew in the first month.",
            ".xlsx": "| Month | Trips |\n| --- | --- |\n| May | 420 |",
        }
        for suffix, text in cases.items():
            with self.subTest(suffix=suffix):
                content = content_of(dict(path="shuttle_report" + suffix, content=text))
                self.assertEqual(content.title, "Shuttle report")
                self.assertFalse(
                    any(s.heading == "shuttle_report" for s in content.sections)
                )
                raw = built(content, suffix)
                self.assertEqual(readback.read(raw, suffix).title, "Shuttle report")
        # A strict content object with a slug title is written in words too.
        value = normalize(
            dict(
                path="shuttle_report.docx",
                sources=[],
                content=json.dumps(
                    dict(
                        title="shuttle_report",
                        sections=[dict(heading="Trips", paragraphs=["420 in May"])],
                    )
                ),
            )
        )
        self.assertEqual(value["content"]["title"], "Shuttle report")

    def test_verify_and_the_deliverable_check_refuse_a_slug_title(self) -> None:
        content = Content.model_validate(
            dict(
                title="presentation-kestrel",
                sections=[dict(heading="Kestrel Project", bullets=["A shuttle"])],
            )
        )
        with TemporaryDirectory() as directory:
            for suffix in (".pptx", ".docx", ".xlsx", ".pdf"):
                with self.subTest(suffix=suffix):
                    with self.assertRaisesRegex(VerificationError, "file_name_title"):
                        render(
                            content,
                            Path(directory) / ("deck" + suffix),
                            image_root=Path(directory),
                        )
        self.assertEqual(len(title_gaps(content)), 1)
        self.assertEqual(
            title_gaps(content.model_copy(update=dict(title="Kestrel"))), []
        )
        self.assertEqual(
            len(
                title_gaps(
                    content.model_copy(update=dict(title="kestrel")), "kestrel.pptx"
                )
            ),
            1,
        )


class SourcesTests(unittest.TestCase):
    def test_the_excerpts_go_in_the_notes_of_the_closing_slide(self) -> None:
        content = include_sources(
            content_of(
                dict(path="kestrel.pptx", title="Kestrel Project", slides=SLIDES)
            ),
            EXCERPTS,
            deck=True,
        )
        headings = [section.heading for section in content.sections]
        self.assertEqual(headings, [slide["title"] for slide in SLIDES])
        last = content.sections[-1]
        self.assertEqual(last.bullets, ("Questions from the steering board",))
        self.assertEqual(kind(last, len(headings) - 1, len(headings)), "closing")
        deck = deck_of(built(content, sources=EXCERPTS))
        self.assertEqual(len(deck.slides), len(SLIDES))
        self.assertNotIn("Sources", [heading(slide) for slide in deck.slides])
        notes = " ".join(deck.slides[-1].notes_slide.notes_text_frame.text.split())
        for excerpt in EXCERPTS:
            self.assertIn(excerpt, notes)
        # Published again with the same source: nothing is added twice.
        self.assertEqual(include_sources(content, EXCERPTS, deck=True), content)

    def test_the_last_slide_keeps_its_layout_and_its_own_notes(self) -> None:
        """Without a closing slide the excerpts follow the notes of the last
        slide, whose lines may quote the source: it is drawn as it would be
        without them (never as a Sources list)."""
        slides = [
            *SLIDES[:-1],
            {
                "title": "Calendar",
                "bullets": [
                    "Goal: halve single-occupancy car trips by the end of 2027.",
                    "Indicators: shared trips per week, occupancy, satisfaction.",
                ],
                "notes": "Say the dates aloud.",
            },
        ]
        bare = content_of(dict(path="kestrel.pptx", title="Kestrel", slides=slides))
        content = include_sources(bare, EXCERPTS, deck=True)
        count = len(content.sections)
        self.assertEqual(count, len(slides))
        self.assertEqual(content.sections[-1].notes[0], "Say the dates aloud.")
        self.assertFalse(quoted(content.sections[-1]))
        self.assertEqual(
            kind(content.sections[-1], count - 1, count),
            kind(bare.sections[-1], count - 1, count),
        )
        self.assertEqual(len(deck_of(built(content, sources=EXCERPTS)).slides), count)

    def test_a_sources_slide_of_an_earlier_deck_is_still_a_list(self) -> None:
        """A deck delivered before staging -151 had a Sources slide quoting
        the opening of each excerpt: read back, it is rebuilt as a list,
        never as the closing slide (staging -145)."""
        sources = Content.model_validate(
            dict(
                title="Kestrel",
                sections=[
                    dict(heading="Kestrel", bullets=["A shared shuttle"]),
                    dict(
                        heading="Sources",
                        bullets=[EXCERPTS[1]],
                        notes=[" ".join(EXCERPTS)],
                    ),
                ],
            )
        ).sections[-1]
        self.assertTrue(quoted(sources))
        self.assertEqual(kind(sources, 1, 3), "content")

    def test_a_deck_with_sources_reads_back_and_rebuilds_the_same(self) -> None:
        content = include_sources(
            content_of(
                dict(path="kestrel.pptx", title="Kestrel Project", slides=SLIDES)
            ),
            EXCERPTS,
            deck=True,
        )
        raw = built(content)
        again = built(
            content_of(
                dict(
                    json.loads(
                        json.dumps(readback.editable(readback.read(raw, ".pptx")))
                    ),
                    path="kestrel.pptx",
                )
            )
        )
        self.assertEqual(
            hashlib.sha256(again).hexdigest(), hashlib.sha256(raw).hexdigest()
        )


class WholeWordsTests(unittest.TestCase):
    def test_narrow_cards_keep_every_word_whole(self) -> None:
        content = content_of(
            dict(path="kestrel.pptx", title="Kestrel Project", slides=SLIDES)
        )
        deck = deck_of(built(content))
        self.assertEqual(overflowing(deck), [])

    def test_a_word_too_long_for_four_columns_widens_the_layout(self) -> None:
        compound = "Datenschutzgrundverordnungskonformitätsprüfung"
        slides = [
            SLIDES[0],
            {
                "title": "Risiken",
                "bullets": [
                    f"{compound} fehlt",
                    "Geringe Nutzung",
                    "Standortdaten",
                    "Kommunikation",
                ],
            },
            {"title": "Danke", "bullets": ["Fragen des Lenkungskreises"]},
        ]
        content = content_of(dict(path="kestrel.pptx", title="Kestrel", slides=slides))
        deck = deck_of(built(content))
        self.assertEqual(overflowing(deck), [])
        points = [s for s in deck.slides[1].shapes if s.name == "atlas:point"]
        widths = {round(shape.width) for shape in points}
        # Two cards per row at most, or a list across the slide.
        self.assertTrue(not points or max(widths) > deck.slide_width / 3)

    def test_a_word_cut_at_a_line_end_is_found_in_the_rendered_text(self) -> None:
        source = "Need for an engaging communication. Protection de la vie privée"
        self.assertEqual(
            split_words(["Need for an engaging communicatio", "n"], source),
            ["communicatio|n"],
        )
        # Two words at a line end, as the source writes them, are not a cut.
        self.assertEqual(split_words(["Protection de", "la vie privée"], source), [])
        self.assertEqual(split_words(["Need for an", "engaging"], source), [])


class NumbersAndSentencesTests(unittest.TestCase):
    def test_numbers_and_their_units_never_break(self) -> None:
        self.assertEqual(
            bound("3 000 staff, 12 400 €, 20 % and 2026 2027, 20 steps"),
            "3\u00a0000 staff, 12\u00a0400\u00a0€, 20\u00a0% and 2026 2027, 20 steps",
        )
        self.assertEqual(
            words(bound("a campus of 2 400 people")),
            ["a", "campus", "of", "2\u00a0400", "people"],
        )
        font()
        # Each width that could break the number between "2" and "400".
        for width in range(60, 200, 4):
            lines = wrap("for the staff of a campus of 2 400 people", width, 12)
            self.assertFalse(any(line.endswith(" 2") for line in lines), lines)
            self.assertTrue(any("2\u00a0400" in line for line in lines), lines)
        content = content_of(
            dict(path="kestrel.pptx", title="Kestrel Project", slides=SLIDES)
        )
        deck = deck_of(built(content))
        cover = " ".join(
            shape.text_frame.text
            for shape in deck.slides[0].shapes
            if shape.has_text_frame
        )
        self.assertIn("2\u00a0400 people", cover)
        self.assertEqual(overflowing(deck), [])
        document = content_of(
            dict(
                path="report.docx",
                title="Report",
                content="A budget of 9 800 € and 20 % more.",
            )
        )
        from docx import Document
        import io

        paragraphs = " ".join(
            p.text for p in Document(io.BytesIO(built(document, ".docx"))).paragraphs
        )
        self.assertIn("9\u00a0800\u00a0€", paragraphs)
        self.assertIn("20\u00a0%", paragraphs)

    def test_a_number_cut_at_a_line_end_is_found(self) -> None:
        source = "a campus of 2 400 people; halve trips by 20 % in 2027"
        self.assertEqual(
            split_words(
                ["a campus of 2", "400 people; halve trips by 20", "% in 2027"], source
            ),
            ["2|400", "20|%"],
        )
        self.assertEqual(split_words(["halve trips in", "2027"], source), [])
        # Two cells of a table, one under the other, are not one number.
        self.assertEqual(split_words(["Q3", "120", "2", "400"], "Q3\n120\n2\n400"), [])


if __name__ == "__main__":
    unittest.main()
