"""Graduated compaction: spacing, margins, tables, then body type down to
10 pt, tried by code in turn until a report fits the pages asked; its text
is never cut by code (staging -170: a table version three lines over one
page was already compact and nothing more was tried). The content is
fictional."""

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
from packages.doc_render.renderer import COMPACT_LEVELS, compact_level
from packages.doc_render.schema import Content
from services.orchestrator import expectations
from services.orchestrator.project_document_tools import compacted_to_size
from services.retrieval.deliverable_render import Rendered

REPORT = Content.model_validate(
    dict(
        title="Short Weeks at Kestrel Lane",
        sections=[
            dict(heading="Summary", paragraphs=["The office kept its output. " * 6]),
            dict(
                heading="Orders",
                tables=[
                    dict(
                        headers=["Quarter", "Orders"],
                        rows=[["Q1", "41"], ["Q2", "47"], ["Q3", "52"]],
                    )
                ],
            ),
        ],
    )
)
SIZE = expectations.Requirement(
    "R1",
    "About one page",
    1,
    method="code",
    check={"part": "size", "unit": "pages", "count": 1, "relation": "about"},
)


def built(level: int) -> Any:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "r.docx"
        render(REPORT, path, image_root=Path(directory), compact=level)
        return Document(str(path)), path.read_bytes()


class LevelTests(unittest.TestCase):
    def test_each_level_adds_to_the_one_before(self) -> None:
        for level in range(COMPACT_LEVELS + 1):
            with self.subTest(level=level):
                document, _ = built(level)
                self.assertEqual(compact_level(document), level)
                heading = document.styles["Heading 1"]
                self.assertEqual(heading.font.size.pt, 14)
                normal = document.styles["Normal"].font.size
                cells = [
                    run.font.size
                    for row in document.tables[0].rows
                    for cell in row.cells
                    for p in cell.paragraphs
                    for run in p.runs
                ]
                if level >= 4:
                    assert normal is not None
                    self.assertEqual(normal.pt, 10)
                else:
                    self.assertIsNone(normal)
                if level >= 3:
                    self.assertTrue(cells)
                    self.assertTrue(all(s is not None and s.pt == 9 for s in cells))
                    xml = document.tables[0]._tbl.xml
                    self.assertIn("tblCellMar", xml)


class LoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_levels_are_tried_until_one_fits(self) -> None:
        turn = expectations.TurnLedger(
            "k", expectations.Ledger("document", [SIZE], 2), "atlas-qwen", "en"
        )
        tools = SimpleNamespace(
            expectations=turn,
            lang="en",
            file_measures={
                "pages": 2,
                "words": 160,
                "words_per_page": 140,
                "span_permille": 920,
            },
        )
        _, raw = built(0)
        prints = iter([(2, (0.8, 0.1)), (2, (0.8, 0.06)), (1, (0.79,))])

        def rendered(*args: Any, **kwargs: Any) -> Rendered:
            total, filled = next(prints)
            return Rendered(pages=(), total=total, landscape=False, filled=filled)

        with (
            patch(
                "services.retrieval.deliverable_render.render_pages",
                side_effect=rendered,
            ),
            self.assertLogs(
                "services.orchestrator.project_document_tools", "WARNING"
            ) as logs,
        ):
            compact, results = await compacted_to_size(
                tools,
                REPORT,
                ".docx",
                SimpleNamespace(content=raw),
                [expectations.Result("R1", False, "2 pages")],
                [],
                {},
                120,
            )
        self.assertEqual([r.passed for r in results], [True])
        self.assertEqual(compact_level(Document(io.BytesIO(compact.content))), 3)
        levels = [line for line in logs.output if "compact_layout" in line]
        self.assertEqual(len(levels), 3)
        self.assertIn('"level": 3', levels[-1])
        self.assertIn('"fits": true', levels[-1])
        # Text is never cut by code: every word is still there.
        text = " ".join(
            p.text for p in Document(io.BytesIO(compact.content)).paragraphs
        )
        self.assertIn("The office kept its output.", text)


if __name__ == "__main__":
    unittest.main()
