"""Group titles keep their own text, in the order it was written.

Staging -183 (2026-11-23): a Word report printed the three group titles of
its "Key Findings" section one after another as plain paragraphs, then the
nine points of the three lists. A section kept its paragraphs, then its
bullets: the bold title lines ("**Team Wellbeing**") were paragraphs, so
every title came before every point. A bold title line and a deeper Markdown
heading are now level-2 sub-sections (Heading 2 in Word, a smaller heading
in a PDF, kept with their text), a paragraph written after a list goes on
after it (Section.continued), and the form check names group titles left
standing together over one list. The content here is fictional.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
import tempfile
from typing import Any
import unittest

from docx import Document

from packages.doc_render import form, readback, render
from packages.doc_render.schema import Content, label
from services.orchestrator import deliverable_check, expectations
from services.orchestrator.project_document_input import markdown, normalize

REPORT = """# Orchard Rota Pilot

## Overview

The Brambleford orchard club tried a shared watering rota for one summer.

## Key Findings

**Harvest Results**
- Pear yield rose by a fifth
- Fewer trees lost fruit to drought

**Volunteer Experience**

- Rota slots were easy to swap
- Evening slots filled first

**Equipment Notes:**
- Two hoses need new fittings

These findings come from one season only.

## Recommendations

Based on the pilot, we recommend:

- Keeping the rota next summer
- Buying two new fittings
"""


def content_of(text: str, suffix: str) -> Content:
    value = normalize(
        {"title": "Orchard Rota Pilot", "content": text, "path": "rota" + suffix}
    )
    return Content.model_validate(value["content"])


def built(content: Content, suffix: str) -> bytes:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / ("rota" + suffix)
        render(content, path, image_root=Path(directory))
        return path.read_bytes()


def outline(content: Content) -> list[tuple[int, bool, str]]:
    return [(s.level, s.continued, s.heading) for s in content.sections]


class MarkdownGroupTests(unittest.TestCase):
    def test_bold_title_lines_open_sub_sections_with_their_own_points(self) -> None:
        content = content_of(REPORT, ".docx")
        self.assertEqual(
            outline(content),
            [
                (1, False, "Overview"),
                (1, False, "Key Findings"),
                (2, False, "Harvest Results"),
                (2, False, "Volunteer Experience"),
                (2, False, "Equipment Notes"),
                (2, True, "Equipment Notes"),
                (1, False, "Recommendations"),
            ],
        )
        sections = content.sections
        self.assertEqual(
            sections[2].bullets,
            ("Pear yield rose by a fifth", "Fewer trees lost fruit to drought"),
        )
        self.assertEqual(
            sections[3].bullets,
            ("Rota slots were easy to swap", "Evening slots filled first"),
        )
        self.assertEqual(sections[4].bullets, ("Two hoses need new fittings",))
        self.assertEqual(
            sections[5].paragraphs, ("These findings come from one season only.",)
        )
        self.assertFalse(sections[1].has_body())

    def test_deeper_headings_are_sub_sections_whatever_the_top_level(self) -> None:
        for top in ("#", "##", "###"):
            text = (
                f"{top} Plan\n\nIntro text.\n\n{top}# Stage one\n\n- Dig\n\n"
                f"{top}# Stage two\n\n- Plant\n\n{top} Budget\n\nNine euros.\n"
            )
            content = markdown(text, "Garden")
            self.assertEqual(
                outline(content),
                [
                    (1, False, "Plan"),
                    (2, False, "Stage one"),
                    (2, False, "Stage two"),
                    (1, False, "Budget"),
                ],
                top,
            )

    def test_every_block_stays_where_it_was_written(self) -> None:
        text = (
            "## Steps\n\nFirst the frame.\n\n- Cut\n- Sand\n\nThen the paint.\n\n"
            "| Coat | Hours |\n| --- | --- |\n| One | 4 |\n\n- Let it dry\n"
        )
        content = markdown(text, "Shelf")
        self.assertEqual(
            [
                (s.continued, s.paragraphs, s.bullets, len(s.tables))
                for s in content.sections
            ],
            [
                (False, ("First the frame.",), ("Cut", "Sand"), 0),
                (True, ("Then the paint.",), (), 1),
                (True, (), ("Let it dry",), 0),
            ],
        )

    def test_a_bold_sentence_or_a_closing_title_stays_a_paragraph(self) -> None:
        self.assertIsNone(label("**Bring gloves on Saturday.**"))
        self.assertIsNone(label("**Note:** bring gloves"))
        self.assertEqual(label("**Next Steps:**"), "Next Steps")
        content = markdown("## Close\n\nDone.\n\n**Thank you**\n", "Shelf")
        self.assertEqual(content.sections[0].paragraphs, ("Done.", "**Thank you**"))

    def test_a_workbook_keeps_one_sheet_per_heading(self) -> None:
        content = content_of(
            "## Costs\n\n| Item | Euros |\n| --- | --- |\n| Hose | 12 |\n\n"
            "**Paid by the club**\n\nTotal checked in May.\n",
            ".xlsx",
        )
        self.assertEqual(outline(content), [(1, False, "Costs")])

    def test_a_heading_without_text_or_sub_section_is_refused_as_json(self) -> None:
        with self.assertRaises(ValueError) as caught:
            normalize(
                {
                    "path": "rota.docx",
                    "content": {
                        "title": "Rota",
                        "sections": [
                            {"heading": "Empty"},
                            {"heading": "Next", "paragraphs": ["Text."]},
                        ],
                    },
                }
            )
        self.assertIn("deeper sub-headings", str(caught.exception))

    def test_sub_sections_and_continuations_are_not_counted_as_sections(self) -> None:
        content = content_of(REPORT, ".docx")
        self.assertEqual(len(content.sections), 7)
        self.assertEqual(expectations.size_measures(content, REPORT)["sections"], 3)


class RenderedGroupTests(unittest.TestCase):
    def test_word_writes_sub_headings_kept_with_their_points(self) -> None:
        document = Document(io.BytesIO(built(content_of(REPORT, ".docx"), ".docx")))
        rows = [
            (
                p.style.name if p.style else "",
                p.text,
                bool(p.paragraph_format.keep_with_next),
            )
            for p in document.paragraphs
        ]
        start = rows.index(("Heading 1", "Key Findings", True))
        self.assertEqual(
            [(style, text) for style, text, _ in rows[start : start + 10]],
            [
                ("Heading 1", "Key Findings"),
                ("Heading 2", "Harvest Results"),
                ("List Bullet", "Pear yield rose by a fifth"),
                ("List Bullet", "Fewer trees lost fruit to drought"),
                ("Heading 2", "Volunteer Experience"),
                ("List Bullet", "Rota slots were easy to swap"),
                ("List Bullet", "Evening slots filled first"),
                ("Heading 2", "Equipment Notes"),
                ("List Bullet", "Two hoses need new fittings"),
                ("Normal", "These findings come from one season only."),
            ],
        )
        self.assertTrue(
            all(keep for style, _, keep in rows if style.startswith("Heading"))
        )
        self.assertEqual([text for _, text, _ in rows].count("Equipment Notes"), 1)

    def test_both_formats_read_back_and_rebuild_the_same_groups(self) -> None:
        content = content_of(REPORT, ".docx")
        for suffix in (".docx", ".pdf"):
            raw = built(content_of(REPORT, suffix), suffix)
            back = readback.read(raw, suffix)
            self.assertEqual(
                [(s.level, s.heading) for s in back.sections][1:5],
                [
                    (1, "Key Findings"),
                    (2, "Harvest Results"),
                    (2, "Volunteer Experience"),
                    (2, "Equipment Notes"),
                ],
                suffix,
            )
            arguments: dict[str, Any] = json.loads(json.dumps(readback.editable(back)))
            self.assertIn("### Harvest Results", arguments["content"])
            again = content_of(arguments["content"], suffix)
            self.assertEqual(outline(again), outline(content), suffix)

    def test_a_pdf_page_never_ends_with_a_group_title(self) -> None:
        # Wherever the page ends, a heading and its first sub-heading go on
        # to the next page together with the opening of the text.
        line = "The orchard club keeps a log of every watering round."
        for count in range(30, 44):
            text = (
                "\n\n".join([line] * count)
                + "\n\n## Results\n\n### Pears\n\n- More pears\n- Larger pears\n"
            )
            raw = built(content_of(text, ".pdf"), ".pdf")
            measured = form.measure(raw)
            self.assertEqual(form.findings(measured), [], count)
            last = [page.last_line() for page in measured.pages[:-1]]
            self.assertFalse(
                any(found and found.text in ("Results", "Pears") for found in last),
                count,
            )


class MisgroupedTests(unittest.TestCase):
    FLAT = {
        "title": "Rota",
        "sections": [
            {
                "heading": "Key Findings",
                "paragraphs": [
                    "The pilot ran for one summer.",
                    "**Harvest Results**",
                    "Volunteer Experience",
                    "Equipment Notes:",
                ],
                "bullets": ["More pears", "Easy swaps", "Two fittings"],
            }
        ],
    }

    def test_titles_standing_together_over_one_list_are_found(self) -> None:
        content = Content.model_validate(self.FLAT)
        found = form.misgrouped(content)
        self.assertEqual(len(found), 1)
        self.assertIn('"Key Findings" writes 3 group titles', found[0])
        self.assertIn('"Harvest Results"', found[0])
        raw = built(content, ".docx")
        gaps = deliverable_check.form_gaps(".docx", content, raw)
        self.assertEqual(len(gaps), 1)
        self.assertIn("misgrouped", gaps[0])
        self.assertNotIn("\\newpage", gaps[0])

    def test_a_file_printed_with_stacked_titles_reads_back_as_written(self) -> None:
        # A revision of a file built before the fix: its titles stand over
        # one list; the edit keeps their order and the check names them.
        printed: dict[str, Any] = {
            "title": "Rota",
            "sections": [
                {
                    "heading": "Key Findings",
                    "paragraphs": [
                        "**Harvest Results**",
                        "**Volunteer Experience**",
                        "**Equipment Notes**",
                    ],
                    "bullets": ["More pears", "Easy swaps", "Two fittings"],
                },
                {"heading": "Next Steps", "paragraphs": ["The club meets in May."]},
            ],
        }
        raw = built(Content.model_validate(printed), ".docx")
        arguments = readback.editable(readback.read(raw, ".docx"))
        again = content_of(arguments["content"], ".docx")
        self.assertEqual(
            outline(again), [(1, False, "Key Findings"), (1, False, "Next Steps")]
        )
        self.assertEqual(
            again.sections[0].paragraphs,
            (
                "**Harvest Results**",
                "**Volunteer Experience**",
                "**Equipment Notes**",
            ),
        )
        self.assertEqual(len(form.misgrouped(again)), 1)

    def test_a_lead_in_or_grouped_text_is_no_finding(self) -> None:
        self.assertEqual(form.misgrouped(content_of(REPORT, ".docx")), [])
        single = Content.model_validate(
            {
                "title": "Rota",
                "sections": [
                    {
                        "heading": "Plan",
                        "paragraphs": ["The club met twice.", "We recommend:"],
                        "bullets": ["Keep the rota"],
                    }
                ],
            }
        )
        self.assertEqual(form.misgrouped(single), [])


if __name__ == "__main__":
    unittest.main()
