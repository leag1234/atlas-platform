"""Layout requirements of a document or deck are checked by code on the built
file: titles in order, bullets rather than cards, a running footer, a part on
the slide rather than in its notes, counts on each slide (docs/33)."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from typing import Any
import unittest

from packages.doc_render import render
from packages.doc_render.schema import Content, PresentationOutline
from services.orchestrator import expectations
from services.orchestrator.constraints.views import file_view

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "constraints_measure", ROOT / "tools" / "constraints_measure.py"
)
assert SPEC is not None and SPEC.loader is not None
measure = importlib.util.module_from_spec(SPEC)
sys.modules["constraints_measure"] = measure
SPEC.loader.exec_module(measure)

POINTS = [
    "Pack the lanterns in the blue crate before the evening walk",
    "Count the candles twice and write the number on the lid",
    "Leave the matches with the group leader, never in the crate",
]


def deck(bulleted: bool, notes: bool) -> Content:
    sections = []
    for heading in ("Before", "During", "After"):
        case = f"Case: the river group walks at dusk ({heading.lower()})."
        sections.append(
            {
                "heading": heading,
                "bullets": POINTS,
                "bulleted": bulleted,
                "notes": [case] if notes else [],
                "paragraphs": [] if notes else [case],
            }
        )
    return Content.model_validate({"title": "Lantern walk", "sections": sections})


def built(content: Content, suffix: str) -> tuple[bytes, Any]:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / ("file" + suffix)
        render(content, path, image_root=Path(directory))
        raw = path.read_bytes()
        return raw, file_view(path)


def requirement(rid: str, check: dict[str, Any]) -> expectations.Requirement:
    method, normal = expectations.normalized_check("deck", check)
    assert method == "code", check
    return expectations.Requirement(
        rid, "statement " + rid, 1, method=method, check=normal
    )


class Normalized(unittest.TestCase):
    def test_layout_checks_kept_or_sent_to_the_judge(self) -> None:
        ok = expectations.normalized_check(
            "deck",
            {"method": "code", "part": "layout", "rule": "titles", "texts": ["A", "B"]},
        )
        self.assertEqual(ok[0], "code")
        # Nested as the model sometimes writes it.
        nested = expectations.normalized_check(
            "document",
            {
                "method": {
                    "code": {
                        "part": "layout",
                        "rule": "list_style",
                        "style": "bullets",
                        "where": "each",
                    }
                }
            },
        )
        self.assertEqual(
            nested,
            (
                "code",
                {
                    "part": "layout",
                    "rule": "list_style",
                    "style": "bullets",
                    "where": "each",
                },
            ),
        )
        for bad in (
            {"part": "layout", "rule": "font", "texts": ["A"]},
            {"part": "layout", "rule": "titles", "texts": ["A"]},
            {"part": "layout", "rule": "list_style", "style": "stars"},
            {"part": "layout", "rule": "items"},
            {"part": "layout", "rule": "contains", "texts": []},
        ):
            with self.subTest(bad=bad):
                self.assertEqual(
                    expectations.normalized_check("deck", {"method": "code", **bad})[0],
                    "judge",
                )

    def test_exact_texts_must_be_the_persons(self) -> None:
        ledger = expectations.Ledger("deck")
        said = ["Three slides titled «Before», «During» and «After», please."]
        ledger.apply(
            {
                "new": [
                    {
                        "statement": "titles",
                        "method": "code",
                        "part": "layout",
                        "rule": "titles",
                        "texts": ["Before", "During", "After"],
                    },
                    {
                        "statement": "invented",
                        "method": "code",
                        "part": "layout",
                        "rule": "titles",
                        "texts": ["Prepare", "Walk", "Tidy"],
                    },
                ]
            },
            1,
            said,
        )
        methods = [item.method for item in ledger.active()]
        self.assertEqual(methods, ["code", "judge"])


class OnTheBuiltFile(unittest.TestCase):
    def test_bullets_asked_cards_drawn_fail_then_bulleted_pass(self) -> None:
        bullets = requirement(
            "R1",
            {
                "method": "code",
                "part": "layout",
                "rule": "list_style",
                "style": "bullets",
                "where": "each",
            },
        )
        _, cards = built(deck(False, True), ".pptx")
        _, listed = built(deck(True, True), ".pptx")
        failed = expectations.layout_result(bullets, cards)
        self.assertIs(failed.passed, False)
        self.assertIn("no list", failed.how)
        self.assertTrue(expectations.layout_result(bullets, listed).passed)

    def test_a_file_without_readable_text_is_left_to_the_judge(self) -> None:
        # A file whose pages are pictures gives code nothing to measure:
        # neither a pass nor a violation is claimed.
        from services.orchestrator.constraints.views import View

        contains = requirement(
            "R1",
            {
                "method": "code",
                "part": "layout",
                "rule": "contains",
                "texts": ["Draft"],
                "where": "each",
            },
        )
        result = expectations.layout_result(contains, View("pdf", pages=3))
        self.assertIsNone(result.passed)

    def test_titles_counts_and_notes(self) -> None:
        _, on_slide = built(deck(True, False), ".pptx")
        _, in_notes = built(deck(True, True), ".pptx")
        titles = requirement(
            "R1",
            {
                "method": "code",
                "part": "layout",
                "rule": "titles",
                "texts": ["Before", "During", "After"],
            },
        )
        three = requirement(
            "R2",
            {
                "method": "code",
                "part": "layout",
                "rule": "items",
                "count": 3,
                "where": "each",
            },
        )
        case = requirement(
            "R3",
            {
                "method": "code",
                "part": "layout",
                "rule": "contains",
                "texts": ["Case:"],
                "where": "each",
            },
        )
        self.assertTrue(expectations.layout_result(titles, on_slide).passed)
        self.assertTrue(expectations.layout_result(three, on_slide).passed)
        self.assertTrue(expectations.layout_result(case, on_slide).passed)
        self.assertIs(expectations.layout_result(case, in_notes).passed, False)

    def test_running_footer_of_reports(self) -> None:
        report = {
            "title": "Boat shed rota",
            "sections": [{"heading": "Monday", "paragraphs": ["Oars out at nine."]}],
        }
        footer = requirement(
            "R1",
            {
                "method": "code",
                "part": "layout",
                "rule": "contains",
                "texts": ["Shed club"],
                "where": "footer",
            },
        )
        for suffix in (".docx", ".pdf"):
            with self.subTest(suffix=suffix):
                _, without = built(Content.model_validate(report), suffix)
                _, with_footer = built(
                    Content.model_validate({**report, "footer": "Shed club"}), suffix
                )
                self.assertIs(expectations.layout_result(footer, without).passed, False)
                self.assertTrue(expectations.layout_result(footer, with_footer).passed)

    def test_boxes_drawn_in_a_pdf_are_left_to_the_judge(self) -> None:
        content = Content.model_validate(
            {
                "title": "Kit",
                "sections": [{"heading": "Bag", "bullets": ["[ ] Torch", "[ ] Map"]}],
            }
        )
        boxes = requirement(
            "R1",
            {
                "method": "code",
                "part": "layout",
                "rule": "list_style",
                "style": "checkboxes",
            },
        )
        _, pdf = built(content, ".pdf")
        _, document = built(content, ".docx")
        self.assertIsNone(expectations.layout_result(boxes, pdf).passed)
        self.assertTrue(expectations.layout_result(boxes, document).passed)

    def test_labelled_rendered_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            rows = []
            for name, path, raw, met in measure.rendered_cases(Path(directory)):
                view = file_view(path)
                verdict = (
                    None
                    if view is None
                    else measure.checks.check(measure._constraint(raw), view).passed
                )
                rows.append((name, met, verdict))
        tally = measure._tally(rows)
        self.assertEqual(tally["wrong"], [])


class InTheLedger(unittest.TestCase):
    def test_check_document_reads_the_view_and_judges_the_undecided(self) -> None:
        raw, _ = built(deck(False, True), ".pptx")
        ledger = expectations.Ledger("deck")
        ledger.requirements = [
            requirement(
                "R1",
                {
                    "method": "code",
                    "part": "layout",
                    "rule": "list_style",
                    "style": "bullets",
                    "where": "each",
                },
            ),
            requirement(
                "R2",
                {
                    "method": "code",
                    "part": "layout",
                    "rule": "list_style",
                    "style": "checkboxes",
                },
            ),
        ]
        judged: list[Any] = []

        async def complete(payload: dict[str, Any], seconds: float) -> dict[str, Any]:
            judged.append(payload)
            return {
                "text": '{"results": [{"id": "R2", "met": false, "why": "no boxes"}]}'
            }

        turn = expectations.TurnLedger("k", ledger, "profile", "en", complete=complete)
        view = expectations.file_view_of(raw, ".pptx")
        results = asyncio.run(
            turn.check_document(None, "text", 30.0, {}, "walk.pptx", view)
        )
        by_id = {result.id: result for result in results}
        self.assertIs(by_id["R1"].passed, False)
        self.assertIn("no list", by_id["R1"].how)
        # R2 is decided by code on a deck (boxes are text there): no judge call.
        self.assertIs(by_id["R2"].passed, False)
        self.assertEqual(judged, [])
        self.assertIn("bulleted", expectations.describe_check(ledger.requirements[0]))

    def test_bullets_asked_draw_lists(self) -> None:
        ledger = expectations.Ledger("deck")
        ledger.requirements = [
            requirement(
                "R1",
                {
                    "method": "code",
                    "part": "layout",
                    "rule": "list_style",
                    "style": "bullets",
                    "where": "each",
                },
            )
        ]
        tools = SimpleNamespace(
            expectations=expectations.TurnLedger("k", ledger, "p", "en")
        )
        content = expectations.bulleted_as_asked(tools, "walk.pptx", deck(False, True))
        self.assertTrue(all(section.bulleted for section in content.sections))
        _, view = built(content, ".pptx")
        check = requirement(
            "R1",
            {
                "method": "code",
                "part": "layout",
                "rule": "list_style",
                "style": "bullets",
                "where": "each",
            },
        )
        self.assertTrue(expectations.layout_result(check, view).passed)

    def test_outline_carries_bulleted(self) -> None:
        outline = PresentationOutline.model_validate(
            {
                "title": "Walk",
                "slides": [{"title": "Before", "bullets": POINTS, "bulleted": True}],
            }
        )
        self.assertTrue(outline.content().sections[0].bulleted)


if __name__ == "__main__":
    unittest.main()
