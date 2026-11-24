"""A Word report whose print has a nearly empty last page is laid out compact
when that removes it, never cut; a request the local classifier labels "run"
without the words of a run stays in the chat. Content is fictional."""

from __future__ import annotations

import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
from typing import Any
import unittest
from unittest.mock import patch

from docx import Document

from packages.doc_render import render
from packages.doc_render.renderer import compact_level
from packages.doc_render.schema import Content
from services.orchestrator import expectations, request_router
from services.orchestrator.project_document_tools import compacted_tail
from services.retrieval.deliverable_render import Rendered

REPORT = Content.model_validate(
    dict(
        title="Allotment Rota for the Spring Term",
        sections=[
            dict(heading="Overview", paragraphs=["The beds are shared fairly. " * 8]),
            dict(heading="Closing", paragraphs=["Tools go back to the shed. " * 6]),
        ],
    )
)


def built(level: int = 0) -> Any:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "r.docx"
        render(REPORT, path, image_root=Path(directory), compact=level)
        return SimpleNamespace(content=path.read_bytes())


def prints(*pages: tuple[float, ...]) -> Any:
    """What the page print gives on each call, in turn."""
    shown = iter(pages)

    def render_pages(*args: Any, **kwargs: Any) -> Rendered:
        filled = next(shown)
        return Rendered(pages=(), total=len(filled), landscape=False, filled=filled)

    return patch(
        "services.retrieval.deliverable_render.render_pages", side_effect=render_pages
    )


def tools_with(*items: expectations.Requirement) -> Any:
    ledger = expectations.Ledger("document", list(items), len(items) + 1)
    return SimpleNamespace(
        expectations=expectations.TurnLedger("k", ledger, "p", "en"), lang="en"
    )


class TailTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_tail_a_compact_layout_removes_is_removed(self) -> None:
        document = built()
        with (
            prints((0.746, 0.052), (0.7,)),
            self.assertLogs(
                "services.orchestrator.project_document_tools", "WARNING"
            ) as logs,
        ):
            result = await compacted_tail(
                tools_with(), REPORT, ".docx", document, [], {}, 120
            )
        self.assertIsNot(result, document)
        self.assertEqual(compact_level(Document(io.BytesIO(result.content))), 1)
        self.assertTrue(any('"fits": true' in line for line in logs.output))
        # Nothing is cut.
        text = " ".join(p.text for p in Document(io.BytesIO(result.content)).paragraphs)
        self.assertIn("Tools go back to the shed.", text)

    async def test_levels_stop_at_margins_never_smaller_type(self) -> None:
        document = built()
        with (
            prints((0.746, 0.052), (0.75, 0.05), (0.75, 0.04)),
            self.assertLogs("services.orchestrator.project_document_tools", "WARNING"),
        ):
            result = await compacted_tail(
                tools_with(), REPORT, ".docx", document, [], {}, 120
            )
        self.assertIs(result, document)

    async def test_a_page_that_is_not_a_tail_is_left_alone(self) -> None:
        for filled in ((0.9, 0.5), (0.8,), (0.85, 0.2)):
            with self.subTest(filled=filled), prints(filled):
                document = built()
                result = await compacted_tail(
                    tools_with(), REPORT, ".docx", document, [], {}, 120
                )
                self.assertIs(result, document)

    async def test_pages_asked_or_breaks_asked_are_respected(self) -> None:
        asked = expectations.Requirement(
            "R1",
            "Two pages",
            1,
            method="code",
            check={"part": "size", "unit": "pages", "count": 2, "relation": "exact"},
        )
        document = built()
        with prints((0.746, 0.052)):
            self.assertIs(
                await compacted_tail(
                    tools_with(asked), REPORT, ".docx", document, [], {}, 120
                ),
                document,
            )
        broken = Content.model_validate(
            dict(
                title="Allotment Rota",
                sections=[dict(heading="One", paragraphs=["x"], page_break=True)],
            )
        )
        with prints((0.746, 0.052)):
            self.assertIs(
                await compacted_tail(
                    tools_with(), broken, ".docx", document, [], {}, 120
                ),
                document,
            )

    async def test_other_formats_and_a_short_turn_are_left_alone(self) -> None:
        document = built()
        self.assertIs(
            await compacted_tail(tools_with(), REPORT, ".pdf", document, [], {}, 120),
            document,
        )
        self.assertIs(
            await compacted_tail(tools_with(), REPORT, ".docx", document, [], {}, 10),
            document,
        )


class LocalRunTests(unittest.IsolatedAsyncioTestCase):
    def local(self, text: str, kind: str, follow_up: bool = False) -> Any:
        decided = {"kind": kind, "file_format": None, "follow_up": follow_up}
        with patch("services.orchestrator.router_local.decide", return_value=decided):
            return request_router.local_decision(text, [])

    def test_a_question_labelled_run_stays_in_the_chat(self) -> None:
        for text in (
            "and what are the newest findings this year on that? give me sources",
            "et les dernières publications de l'année sur ce sujet, avec les sources ?",
            "welche Neuerungen gab es dieses Jahr dazu? Bitte mit Quellen",
        ):
            with self.subTest(text=text):
                self.assertEqual(self.local(text, "run").kind, "answer")

    def test_a_run_asked_in_words_stays_a_run(self) -> None:
        for text in (
            "write a python script that sorts my invoices by month and run it",
            "écris un script python qui trie mes factures et lance-le",
        ):
            with self.subTest(text=text):
                self.assertEqual(self.local(text, "run").kind, "run")

    def test_a_rerun_and_a_machine_task_keep_their_kind(self) -> None:
        self.assertEqual(self.local("again please", "run", follow_up=True).kind, "run")
        self.assertEqual(
            self.local("check the free space on my server", "remote").kind, "remote"
        )


if __name__ == "__main__":
    unittest.main()
