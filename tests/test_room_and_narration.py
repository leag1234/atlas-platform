"""An addition gets its room before it is written; the answer never tells a
correction of the turn.

Staging -165 (2026-11-22), the multi-turn Word report asked "about one
page": the turn that adds a table printed on two pages. Its writer learnt the
size only from a refusal, the rewrite came back on two pages, and the answer
said that a recommendation "has been restored". Now:

- the file a turn changes comes with the room a page size the person asked
  leaves in it, measured on its print (current_file.room_note);
- a compact layout is tried by the printed height, so a table (height with
  few words) is counted;
- a file answer drops the clauses that tell Atlas's own corrections
  (restored, put back, regenerated...) in the five interface languages.

The content is fictional.
"""

from __future__ import annotations

from decimal import Decimal

import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
from typing import Any
import unittest
from unittest.mock import AsyncMock, Mock, patch

from packages.doc_render import readback, render
from packages.doc_render.schema import Content
from services.orchestrator import current_file, expectations
from services.orchestrator.loop import Limits, Query, Reservation, Turn, run
from services.orchestrator.project_document_tools import compacted_to_size
from services.orchestrator.work_closing import closing_step, without_fix_narration
from services.retrieval.deliverable_render import Rendered

REPORT = Content.model_validate(
    dict(
        title="Short Weeks at Kestrel Lane",
        sections=[
            dict(heading="Summary", paragraphs=["The office kept its output. " * 8]),
            dict(heading="Recommendations", bullets=["Review the hours each quarter"]),
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


def built() -> bytes:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "r.docx"
        render(REPORT, path, image_root=Path(directory))
        return path.read_bytes()


class NarrationTests(unittest.TestCase):
    def test_corrections_of_the_turn_are_left_out(self) -> None:
        cases = {
            "The table was added. Note: the file runs to 2 pages rather than one, "
            "and the recommendation to review the hours has been restored.": (
                "The table was added. Note: the file runs to 2 pages rather than one."
            ),
            "Le tableau a été ajouté. La recommandation a été rétablie.": (
                "Le tableau a été ajouté."
            ),
            "Die Tabelle wurde ergänzt. Der Absatz wurde wiederhergestellt.": (
                "Die Tabelle wurde ergänzt."
            ),
            "Se añadió la tabla. La recomendación fue restaurada.": (
                "Se añadió la tabla."
            ),
            "La tabella è stata aggiunta. La raccomandazione è stata ripristinata.": (
                "La tabella è stata aggiunta."
            ),
            "The report was regenerated and is ready.": "",
        }
        for text, expected in cases.items():
            with self.subTest(text=text[:30]):
                self.assertEqual(without_fix_narration(text), expected)

    def test_the_persons_own_change_and_ordinary_words_stay(self) -> None:
        kept = (
            "The title has been changed to The Kestrel Experiment.",
            "Le tableau indique une remise de 10 % au deuxième trimestre.",
        )
        for text in kept:
            with self.subTest(text=text[:30]):
                self.assertEqual(without_fix_narration(text), text)
        self.assertEqual(
            without_fix_narration(
                "The old title has been restored.", "please restore the old title"
            ),
            "The old title has been restored.",
        )


class RoomTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        environment = patch.dict(os.environ, {"ATLAS_DATA_DIR": directory.name})
        environment.start()
        self.addCleanup(environment.stop)

    async def test_the_writer_gets_the_room_before_adding_a_table(self) -> None:
        raw = built()
        scope = "reader|conversation-21"
        ledger = expectations.Ledger("document", [SIZE], 2)
        expectations.save(expectations.document_key(scope, "kestrel.docx"), 1, ledger)
        current = current_file.CurrentFile(
            "kestrel.docx", ".docx", "f1", "v1", readback.read(raw, ".docx"), raw=raw
        )
        tools = SimpleNamespace(picture_scope=scope)
        with patch(
            "services.retrieval.deliverable_render.render_pages",
            return_value=Rendered(pages=(), total=1, landscape=False, filled=(0.4,)),
        ):
            current.room = await current_file.room_note(tools, current)
        self.assertIn("asked for about 1 page", current.room)
        self.assertIn("prints on 1 page with", current.room)
        self.assertIn("a table row about", current.room)
        self.assertIn("shorten the other sentences evenly", current.room)
        instruction = current.context("add a table")["instruction"]
        self.assertTrue(instruction.endswith(current.room))
        # No page size asked: nothing is measured or said.
        other = current_file.CurrentFile(
            "orchard.docx", ".docx", "f2", "v2", readback.read(raw, ".docx"), raw=raw
        )
        self.assertEqual(await current_file.room_note(tools, other), "")

    async def test_a_table_over_the_page_is_compacted_by_its_height(self) -> None:
        """Few words, much height: the words alone would not try compact."""
        turn = expectations.TurnLedger(
            "k", expectations.Ledger("document", [SIZE], 2), "atlas-qwen", "en"
        )
        result = expectations.Result("R1", False, "2 pages")
        tools = SimpleNamespace(
            expectations=turn,
            lang="en",
            file_measures={
                "pages": 2,
                "words": 150,
                "words_per_page": 90,
                "span_permille": 950,
            },
        )
        document = SimpleNamespace(content=built())
        with patch(
            "services.retrieval.deliverable_render.render_pages",
            return_value=Rendered(pages=(), total=1, landscape=False, filled=(0.78,)),
        ):
            compact, results = await compacted_to_size(
                tools, REPORT, ".docx", document, [result], [], {}, 60
            )
        self.assertIsNot(compact, document)
        self.assertEqual([r.passed for r in results], [True])
        self.assertEqual(tools.file_measures["pages"], 1)


class LoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_answer_of_a_published_file_drops_the_correction(self) -> None:
        published = {
            "trust": "untrusted",
            "deliverable": {
                "id": "f1",
                "file_version_id": "v1",
                "relative_path": "k.docx",
            },
            "text": "[Download k.docx](/d) · [Preview](/p)",
            "file_version_ids": ["v1"],
        }
        from services.orchestrator.loop import Call

        turns = [
            Turn(
                "",
                (
                    Call(
                        "p1",
                        "publish_document",
                        json.dumps({"path": "k.docx", "content": "# K", "sources": []}),
                    ),
                ),
            ),
            Turn("The table was added, and the closing paragraph has been restored."),
        ]

        async def complete(messages: list[dict[str, Any]], timeout: float) -> Turn:
            return turns.pop(0)

        model = Mock(
            estimate=Mock(return_value=Reservation(100, Decimal("0.001"))),
            complete=AsyncMock(side_effect=complete),
        )
        tools = Mock(
            estimate=Mock(return_value=Reservation(0, Decimal(0))),
            execute=AsyncMock(return_value=published),
        )
        selection = Mock(selection=Mock(deliverable_pending=Mock(return_value=False)))
        with patch("services.orchestrator.code_workspace.enabled", return_value=True):
            limits = Limits(
                profile="atlas-qwen",
                tokens=None,
                tool_calls=4,
                wall_clock=30,
                cost=Decimal("1"),
                model_calls=4,
                code_mode=True,
            )
            result = await run(
                Query(question="add a table to the report", lang="en"),
                model,
                tools,
                "",
                limits,
                final_only=True,
                after_tools=lambda executed: closing_step(selection, True, executed),
            )
        self.assertEqual(result.text, "The table was added.")


if __name__ == "__main__":
    unittest.main()
