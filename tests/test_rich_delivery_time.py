"""Rich deliverables within the turn's time, SVG files, pictures of a revision.

Staging -184 (class suite, generated scenarios): a deck with per-slide
constraints, a three-page PDF and a named SVG file ended on the work time
limit with nothing delivered. The checks of a publication ran one after the
other, a refusal asked for a repair the turn had no time left for, a named
SVG file had no format to be published in, and a correction of a PDF drew
its pictures again under other labels and placed the earlier one too. The
content below is fictional.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Iterator
import contextlib
from decimal import Decimal
import io
import json
import os
from pathlib import PurePosixPath
import tempfile
import time
from types import SimpleNamespace
from typing import Any
import unittest
from unittest import mock
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from packages.text_deliverables import (
    TextDeliverableError,
    names_markup_file,
    validate_text,
    visible_text,
)
from services.orchestrator import (
    deliverable_check,
    expectations,
    loop,
    project_document_tools,
    work_pictures,
)
from services.orchestrator.project_document_tools import execute
from services.orchestrator.terminal_client import ProjectPublisher

BODY = "\n\n".join(
    f"## Stall {n}\n\nThe lantern market at Ferrow Quay opens at dusk."
    for n in range(3)
)
SNAIL = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 140">'
    '<circle cx="60" cy="55" r="40" fill="#1b2a4a"/>'
    '<text x="60" y="130" text-anchor="middle">QUAI NORD</text></svg>'
)


def work_tools() -> Mock:
    execution = Mock()
    execution.check = AsyncMock()

    async def public(method: str, path: str) -> tuple[int, dict[str, Any]]:
        if path == "folders":
            return 200, dict(items=[dict(id="folder", revision=0)])
        return 200, dict(deliverables_folder_id="folder")

    execution.broker.public = AsyncMock(side_effect=public)
    execution.broker.org = str(uuid4())
    execution.broker.project = str(uuid4())
    tools = Mock(execution=execution)
    tools.deliverables = []
    tools.requested_suffix = None
    tools.numbered_copies = False
    tools.request_text = "a short guide to the Ferrow Quay lantern market"
    tools.earlier_requests = []
    tools.calculation_refusals = set()
    tools.check_model = Mock(profile="atlas-qwen", spent=Decimal(0), strict=False)
    tools.expectations = None
    tools.picture_scope = "reader|conversation-31"
    tools.lang = "en"
    tools.deliverable_refused = False
    tools.current_file = None
    tools.turn_files = {}
    tools.built_titles = {}
    tools.deliverable_checks = 0
    return tools


@contextlib.contextmanager
def published() -> Iterator[AsyncMock]:
    publish = AsyncMock(
        side_effect=lambda document, name, *rest: dict(
            id=str(uuid4()), file_version_id=str(uuid4()), relative_path=name
        )
    )
    with (
        patch.object(ProjectPublisher, "publish", publish),
        patch(
            "services.orchestrator.file_results.project_deliverable_links",
            return_value="[file](link)",
        ),
    ):
        yield publish


class TurnTimeTests(unittest.TestCase):
    def test_the_loop_records_the_turn_end_and_the_writing_time(self) -> None:
        tools = SimpleNamespace()
        loop.turn_time(tools, 90.0, 41.5)
        self.assertAlmostEqual(tools.turn_deadline - time.monotonic(), 90.0, delta=1)
        self.assertEqual(tools.writing_seconds, 41.5)
        # Tools that take no attribute are left alone.
        loop.turn_time(object(), 10.0, 1.0)

    def test_a_repair_is_asked_only_when_it_fits(self) -> None:
        tools = SimpleNamespace(deliverable_refused=False)
        # Outside a Work turn nothing is measured: a refusal stays possible.
        self.assertTrue(project_document_tools.repair_fits(tools, 30))
        self.assertFalse(project_document_tools.keep_for_delivery(tools, 30))
        tools.turn_deadline = time.monotonic() + 200
        tools.writing_seconds = 70.0
        self.assertTrue(project_document_tools.repair_fits(tools, 40))
        tools.turn_deadline = time.monotonic() + 80
        self.assertFalse(project_document_tools.repair_fits(tools, 40))
        with self.assertLogs(project_document_tools.__name__, "WARNING") as logged:
            self.assertTrue(project_document_tools.keep_for_delivery(tools, 40))
        self.assertIn("repair_skipped", logged.output[0])
        # The turn's one refusal is spent: neither check refuses the file now.
        self.assertTrue(tools.deliverable_refused)
        outcome = deliverable_check.Outcome(gaps=["slide 3 has two bullets"])
        outcome.certain = list(outcome.gaps)
        note = deliverable_check.decide(tools, outcome)
        self.assertIn("slide 3 has two bullets", note)

    def test_an_identical_version_is_refused_even_late(self) -> None:
        """Staging -185: a correction came back byte for byte the same file and
        was delivered as the change; it is refused once, whatever the time."""
        tools = SimpleNamespace(
            deliverable_refused=False,
            turn_deadline=time.monotonic() + 30,
            writing_seconds=90.0,
        )
        self.assertFalse(project_document_tools.repair_fits(tools, 20))
        same = {"identical": True, "removed": [], "replaced": [], "added": []}
        self.assertFalse(project_document_tools.keep_for_delivery(tools, 20, same))
        self.assertFalse(tools.deliverable_refused)
        outcome = deliverable_check.Outcome(
            gaps=[deliverable_check.IDENTICAL.format(name="harbour-lanterns.pptx")]
        )
        outcome.certain = list(outcome.gaps)
        with self.assertRaises(ValueError):
            deliverable_check.decide(tools, outcome)
        # The repair came back identical again: published, the gap said.
        self.assertFalse(project_document_tools.keep_for_delivery(tools, 20, same))
        self.assertIn("identical", deliverable_check.decide(tools, outcome))
        # A changed version late in the turn is still kept for delivery.
        late = SimpleNamespace(
            deliverable_refused=False,
            turn_deadline=time.monotonic() + 30,
            writing_seconds=90.0,
        )
        changed = {
            "identical": False,
            "removed": ["Old line"],
            "replaced": [],
            "added": [],
        }
        with self.assertLogs(project_document_tools.__name__, "WARNING"):
            self.assertTrue(project_document_tools.keep_for_delivery(late, 20, changed))


class PublicationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        environment = patch.dict(os.environ, {"ATLAS_DATA_DIR": directory.name})
        environment.start()
        self.addCleanup(environment.stop)

    async def test_the_two_checks_run_side_by_side(self) -> None:
        events: list[str] = []

        async def ledger(*args: Any, **kwargs: Any) -> list[Any]:
            events.append("ledger start")
            await asyncio.sleep(0.3)
            events.append("ledger end")
            return []

        async def review(*args: Any, **kwargs: Any) -> Any:
            events.append("review start")
            await asyncio.sleep(0.3)
            events.append("review end")
            return deliverable_check.Outcome(checked=True)

        arguments = dict(
            path="lanterns.docx", title="Lanterns", content=BODY, sources=[]
        )
        with (
            published(),
            patch.object(expectations, "document_check", ledger),
            patch.object(deliverable_check, "review", review),
            patch(
                "services.orchestrator.work_save.project_file_paths",
                AsyncMock(return_value={}),
            ),
        ):
            result = await execute(
                work_tools(), "publish_document", json.dumps(arguments), 120
            )
        self.assertIn("deliverable", result)
        # Both started before either ended: side by side, not one after the other.
        self.assertEqual(sorted(events[:2]), ["ledger start", "review start"])
        self.assertEqual(sorted(events[2:]), ["ledger end", "review end"])

    async def test_gaps_found_late_in_the_turn_are_said_not_refused(self) -> None:
        async def review(*args: Any, **kwargs: Any) -> Any:
            return deliverable_check.Outcome(
                gaps=["the Opening hours section has no closing time"],
                certain=["the Opening hours section has no closing time"],
                checked=True,
            )

        tools = work_tools()
        # Writing the file took 70 s; 50 s are left in the turn.
        tools.turn_deadline = time.monotonic() + 50
        tools.writing_seconds = 70.0
        arguments = dict(path="market.docx", title="Market", content=BODY, sources=[])
        with (
            published(),
            patch.object(expectations, "document_check", AsyncMock(return_value=[])),
            patch.object(deliverable_check, "review", review),
            patch(
                "services.orchestrator.work_save.project_file_paths",
                AsyncMock(return_value={}),
            ),
        ):
            result = await execute(tools, "publish_document", json.dumps(arguments), 45)
        self.assertIn("deliverable", result)
        self.assertIn("no closing time", result["note"])
        # With the time for a repair, the same gap is refused once.
        early = work_tools()
        early.turn_deadline = time.monotonic() + 230
        early.writing_seconds = 70.0
        with (
            published(),
            patch.object(expectations, "document_check", AsyncMock(return_value=[])),
            patch.object(deliverable_check, "review", review),
            patch(
                "services.orchestrator.work_save.project_file_paths",
                AsyncMock(return_value={}),
            ),
            self.assertRaises(ValueError),
        ):
            await execute(early, "publish_document", json.dumps(arguments), 120)

    async def test_a_named_svg_file_is_published_as_its_markup(self) -> None:
        arguments = dict(path="quai-nord.svg", content=SNAIL, sources=[])
        with (
            published() as publish,
            patch.object(expectations, "document_check", AsyncMock(return_value=[])),
            patch.object(
                deliverable_check,
                "review",
                AsyncMock(return_value=deliverable_check.Outcome(checked=True)),
            ),
            patch(
                "services.orchestrator.work_save.project_file_paths",
                AsyncMock(return_value={}),
            ),
        ):
            result = await execute(
                work_tools(), "publish_document", json.dumps(arguments), 60
            )
        self.assertEqual(result["deliverable"]["relative_path"], "quai-nord.svg")
        assert publish.await_args is not None
        document = publish.await_args.args[0]
        self.assertEqual(document.content, SNAIL.encode())

    async def test_a_format_no_publication_writes_is_named(self) -> None:
        for path in ("poster.png", "plan.dwg", "notes.odt"):
            with self.subTest(path=path):
                with self.assertRaises(ValueError) as refused:
                    await execute(
                        work_tools(),
                        "publish_document",
                        json.dumps(dict(path=path, content="x", sources=[])),
                        30,
                    )
                message = str(refused.exception)
                self.assertIn("unsupported_format", message)
                self.assertIn(".svg", message)
                self.assertNotIn("Use Markdown content", message)

    async def test_a_chat_revision_near_the_deadline_keeps_its_version(self) -> None:
        captured: list[float] = []

        async def approved(
            execution: Any, tools: Any, effect: Any, deadline: float, **kw: Any
        ) -> Any:
            captured.append(deadline)
            return None, "unattended"

        tools = work_tools()
        tools.numbered_copies = True
        tools.revises_delivered = True
        tools.turn_files = {"guide.docx": "guide.docx"}
        tools.execution.broker.public = AsyncMock(
            return_value=(
                200,
                dict(
                    id="f1",
                    folder_id="folder",
                    relative_path="guide.docx",
                    version_id="v1",
                    revision=1,
                ),
            )
        )
        deadline = time.monotonic() + 12
        with patch("services.orchestrator.work_save.approved_overwrite", approved):
            await project_document_tools.publish_revision(
                tools,
                Mock(),
                SimpleNamespace(content=b"x"),
                PurePosixPath("guide.docx"),
                "folder",
                {"folder/guide.docx": dict(file_id="f1")},
                "regenerated",
                [],
                deadline,
            )
        # Given on the person's behalf at once: not 20 s before the deadline.
        self.assertEqual(
            captured, [deadline - project_document_tools.REVISION_APPROVAL_SECONDS]
        )


class SvgTests(unittest.TestCase):
    def test_a_well_formed_svg_is_its_own_text(self) -> None:
        self.assertEqual(validate_text(SNAIL.encode(), ".svg"), SNAIL)
        self.assertEqual(visible_text(SNAIL, ".svg"), "QUAI NORD")
        titled = (
            '<svg xmlns="http://www.w3.org/2000/svg"><title>Moth lamp</title>'
            "<text>MARSH  GATE</text></svg>"
        )
        self.assertEqual(visible_text(titled, ".svg"), "Moth lamp MARSH GATE")

    def test_named_failures(self) -> None:
        for raw, code in (
            (b"<svg><circle></svg>", "svg_not_well_formed"),
            (b"<html><body/></html>", "svg_root_missing"),
            (
                b'<!DOCTYPE svg [<!ENTITY a "aaaa">]><svg>&a;</svg>',
                "svg_declaration_not_allowed",
            ),
            (b"plain words", "svg_not_well_formed"),
        ):
            with self.subTest(code=code):
                with self.assertRaises(TextDeliverableError) as caught:
                    validate_text(raw, ".svg")
                self.assertEqual(caught.exception.code, code)

    def test_a_download_of_an_svg_never_renders(self) -> None:
        from pathlib import Path

        source = Path("services/retrieval/project_files.py").read_text()
        self.assertIn('{"text/html", "image/svg+xml"}', source)

    def test_a_named_markup_file_keeps_its_code_block(self) -> None:
        for text in (
            "Peux-tu me livrer un fichier SVG nommé `phare-du-nord.svg` ?",
            "Send me tide-table.csv please",
            "Schick mir bitte die Datei kalender.html",
        ):
            self.assertTrue(names_markup_file(text), text)
        for text in (
            "Make me a poster of a lighthouse",
            "Une présentation de 3 diapositives, s.v.p.",
            "Version 2.0 of the plan, as a PDF",
        ):
            self.assertFalse(names_markup_file(text), text)


class FakeServer:
    """The picture server, one plain picture per call, no wait."""

    PICTURE_SECONDS = 1.0

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def health(self) -> dict[str, Any]:
        return {"ready": True, "seconds_per_image": 0.0, "busy_seconds": 0.0}

    def expected_wait(self, status: dict[str, Any], pictures: int) -> float:
        return 0.0

    async def render_set(self, prompt: str, seeds: list[int], wait: float) -> Any:
        from PIL import Image

        from services.orchestrator.pictures.cpu_server import Rendered

        self.prompts.append(prompt)
        output = io.BytesIO()
        Image.new("RGB", (32, 32), "teal").save(output, "PNG")
        image = base64.b64encode(output.getvalue()).decode()
        return [Rendered(image, 0.01, 0.0, 0.0, 0.1)]


def items(*labels: str) -> str:
    return json.dumps(
        {
            "items": [
                {"label": label, "description": f"a plain drawing of {label}"}
                for label in labels
            ],
            "style": "ink",
        }
    )


class RevisionPictureTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        patcher = mock.patch.dict(os.environ, {"ATLAS_WORK_PICTURE_DIR": folder.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def tools(self) -> SimpleNamespace:
        return SimpleNamespace(
            check_model=SimpleNamespace(
                profile="atlas-qwen", strict=True, spent=Decimal(0)
            ),
            picture_scope="reader|chat-loom",
            sink=None,
            lang="de",
            pictures_pending=[],
        )

    async def test_the_same_item_in_other_words_is_reused(self) -> None:
        server = FakeServer()
        first = await work_pictures.execute(
            self.tools(),
            items("Webrahmen mit gespannten Kettfäden"),
            900,
            server=server,
        )
        self.assertEqual(len(server.prompts), 1)
        again = await work_pictures.execute(
            self.tools(),
            items(
                "Nahansicht Webrahmen mit gespannten Kettfäden",
                "Nahansicht Webrahmen mit blauem Schussfaden",
            ),
            900,
            server=server,
        )
        # Only the item not drawn before is drawn.
        self.assertEqual(len(server.prompts), 2)
        self.assertEqual([p["reused"] for p in again["pictures"]], [True, False])
        self.assertEqual(
            again["pictures"][0]["reference"], first["pictures"][0]["reference"]
        )

    async def test_an_ambiguous_label_is_drawn_anew(self) -> None:
        server = FakeServer()
        await work_pictures.execute(
            self.tools(), items("red kite", "red kite nest"), 900, server=server
        )
        again = await work_pictures.execute(
            self.tools(), items("kite"), 900, server=server
        )
        # "kite" could be either earlier picture: drawn again.
        self.assertEqual(len(server.prompts), 3)
        self.assertFalse(again["pictures"][0]["reused"])
        # Different items keep their own pictures.
        self.assertFalse(work_pictures._same_item("Step 2", "Step 3"))
        self.assertTrue(work_pictures._same_item("Hen", "little hen"))

    def test_an_earlier_picture_is_placed_only_where_one_is_missing(self) -> None:
        self.assertIn("never two pictures for one item", work_pictures.EARLIER)
        self.assertNotIn("Place each one too", work_pictures.EARLIER)


if __name__ == "__main__":
    unittest.main()
