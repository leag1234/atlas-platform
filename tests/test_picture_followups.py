"""Follow-ups on a picture: understood with their conversation, never a
claimed change that did not happen, the latest version, never the HTML
artifact editor (staging conversation of 2026-11-15, its words not kept).

The model's reading of each phrasing is checked live (the supervisor's
staging acceptance); here the fake call answers as the reading would, and
the tests hold what code decides: which path runs, what the calls receive,
what the answer says."""

from __future__ import annotations

import base64
import io
import json
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

from PIL import Image

from services.orchestrator.interactions import Interaction
from services.orchestrator.loop import GatewayError
from services.orchestrator.pictures import (
    cpu_checks,
    cpu_server,
    cpu_turn,
    dialogue,
    rewrite,
    store,
    texts,
)
from services.orchestrator.pictures import turn as picture_turn
from services.orchestrator.pictures.followup import process_picture_followup
import test_picture_cpu
from test_picture_cpu import CpuGateway, FakeServer, png, rewrite_reply
from test_picture_turn import Request
from picture_jobs import memoise_drafts

FIRST = "A single gardener in a green coat waters a row of tulips in a garden."
REQUIREMENTS = ["a single gardener", "a green coat", "the whole head is visible"]


def follow_reply(
    kind: str,
    change: str = "",
    requirements: list[str] | None = None,
    texts: list[str] | None = None,
) -> str:
    return json.dumps(
        {
            "kind": kind,
            "change": change,
            "requirements": REQUIREMENTS if requirements is None else requirements,
            "texts": texts or [],
        }
    )


class DialogueGateway(CpuGateway):
    """The CPU gateway plus the follow-up reading and the review."""

    def __init__(
        self, follow: str | Exception = "", review: str | Exception = ""
    ) -> None:
        super().__init__(rewrite_reply(prompt=FIRST))
        self.follow = follow or follow_reply("change", "Show the whole head.")
        self.review = review or json.dumps(
            {
                "answer": "",
                "shown": ["a single gardener"],
                "missing": ["the whole head is visible: the head is cut at the top"],
                "other": [],
                "plan": ["Move the gardener down so the head is inside the picture"],
            }
        )
        self.follows: list[dict[str, Any]] = []
        self.reviews: list[dict[str, Any]] = []

    async def __call__(self, payload: dict[str, Any], seconds: float) -> dict[str, Any]:
        system = str(payload["messages"][0]["content"])
        usage = {"prompt_tokens": 600, "completion_tokens": 80}
        for prompt, answer, seen in (
            (dialogue.FOLLOW_SYSTEM, self.follow, self.follows),
            (dialogue.REVIEW_SYSTEM, self.review, self.reviews),
        ):
            # The person's register may follow the instructions (in_register).
            if system.startswith(prompt):
                seen.append(payload)
                if isinstance(answer, Exception):
                    raise answer
                return {"text": answer, "usage": usage, "cost_eur": "0.0004"}
        return await super().__call__(payload, seconds)


class CheckingDialogueGateway(DialogueGateway):
    """The dialogue gateway plus the picture check (replies in order)."""

    def __init__(self, follow: str | Exception = "") -> None:
        super().__init__(follow)
        self.met: list[str] = []

    def payloads_of(self, system: str) -> list[dict[str, Any]]:
        return [p for p in self.payloads if p["messages"][0]["content"] == system]

    async def __call__(self, payload: dict[str, Any], seconds: float) -> dict[str, Any]:
        if payload["messages"][0]["content"] == cpu_checks.SYSTEM:
            self.payloads.append(payload)
            answer = self.met.pop(0) if self.met else '{"met": [true]}'
            return {"text": answer, "usage": {}, "cost_eur": "0.0003"}
        return await super().__call__(payload, seconds)


class SameServer(FakeServer):
    """Draws the very same picture for every prompt and seed."""

    async def render_set(
        self, prompt: str, seeds: list[int], max_wait: float
    ) -> list[cpu_server.Rendered | None]:
        self.jobs.append((list(seeds), max_wait))
        out: list[cpu_server.Rendered | None] = []
        for seed in seeds:
            self.renders.append((prompt, seed))
            out.append(
                cpu_server.Rendered(
                    image=png((10, 120, 200)),
                    nsfw=0.0001,
                    render_seconds=20.0,
                    queue_seconds=0.0,
                    filter_seconds=0.1,
                )
            )
        return out


class EditServer(FakeServer):
    """A server whose health says it edits a reference picture."""

    def __init__(self, fail_edit: bool = False, echo: bool = False) -> None:
        super().__init__()
        self.fail_edit = fail_edit
        # echo: the edit gives back the very picture it was given.
        self.echo = echo
        self.edits: list[tuple[str, int, str]] = []

    async def health(self, seconds: float = 4.0) -> dict[str, Any]:
        status = await super().health(seconds)
        return status | {"edit": True, "seconds_per_edit": 48.0}

    async def render_edit(
        self, prompt: str, seed: int, reference: str, max_wait: float
    ) -> cpu_server.Rendered:
        self.edits.append((prompt, seed, reference))
        if self.fail_edit:
            raise cpu_server.CpuServerError("picture_cpu_http_502")
        return cpu_server.Rendered(
            # Each edit its own picture (or the reference itself, echo).
            image=reference
            if self.echo
            else png((200, (25 * len(self.edits)) % 250, 30)),
            nsfw=0.0001,
            render_seconds=47.0,
            queue_seconds=0.0,
            filter_seconds=0.1,
        )


def current(answer: str) -> Any:
    from services.orchestrator.artifact_tools import current_artifact_message

    return current_artifact_message(
        [{"role": "assistant", "content": answer}, {"role": "user", "content": ""}],
        "owner@example.test",
    )


class Base(unittest.IsolatedAsyncioTestCase):
    """The CPU picture turn's environment and helpers (as test_picture_cpu)."""

    def setUp(self) -> None:
        memoise_drafts(self)
        self.folder = Path(tempfile.mkdtemp(prefix="picture-followups-"))
        self.addCleanup(shutil.rmtree, self.folder, True)
        environment = test_picture_cpu.CPU_ENVIRONMENT | {
            "ATLAS_IMAGE_DIR": str(self.folder / "images"),
            "ATLAS_PICTURE_DIR": str(self.folder / "sets"),
            "ATLAS_DATA_DIR": str(self.folder / "data"),
            "ATLAS_ARTIFACTS": "1",
        }
        patcher = patch.dict(os.environ, environment)
        patcher.start()
        self.addCleanup(patcher.stop)

    def serve(self, server: FakeServer) -> None:
        for name in ("health", "render_set"):
            patcher = patch.object(cpu_server, name, getattr(server, name))
            patcher.start()
            self.addCleanup(patcher.stop)

    async def run_turn(
        self, gateway: CpuGateway, server: FakeServer, text: str
    ) -> Interaction:
        from services.orchestrator.pictures import turn

        self.serve(server)
        item = Interaction()
        await turn.process_picture_turn(Request(text), item, complete=gateway)
        return item

    def picture_set(self, item: Interaction) -> store.PictureSet:
        picture_set = store.by_artifact(str(item.artifacts[0]["id"]))
        assert picture_set is not None
        return picture_set


class FollowUpTests(Base):
    """The CPU picture conversation, then its follow-ups."""

    def serve(self, server: FakeServer) -> None:
        super().serve(server)
        if isinstance(server, EditServer):
            patcher = patch.object(cpu_server, "render_edit", server.render_edit)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def start(
        self, gateway: DialogueGateway, server: FakeServer
    ) -> tuple[Interaction, store.PictureSet]:
        first = await self.run_turn(gateway, server, "un jardinier en manteau vert")
        return first, self.picture_set(first)

    def current(self, answer: str) -> Any:
        return current(answer)

    async def follow(
        self,
        gateway: DialogueGateway,
        history: list[tuple[str, str]],
        text: str,
        *,
        legacy: bool = False,
        routed: Any = None,
    ) -> tuple[bool, Interaction]:
        from services.orchestrator.artifact_tools import current_artifact_message

        request = Request(text, history)
        if routed is not None:
            request.__dict__["model_route"] = routed
        messages: list[dict[str, object]] = [
            {"role": r, "content": t} for r, t in history
        ]
        messages.append({"role": "user", "content": text})
        found = current_artifact_message(messages, "owner@example.test")
        picture_set = dialogue.picture_of(request, found)
        assert picture_set is not None
        item = Interaction()
        done = await dialogue.follow_up(
            request,
            item,
            found,
            picture_set,
            routed,
            legacy_change=legacy,
            complete=gateway,
        )
        return done, item

    # (a) Contextual follow-ups take the picture path with their context.

    def test_understood_reads_the_model_declared_texts(self) -> None:
        understood = dialogue.parse_understood(
            follow_reply(
                "change",
                "Replace the caption with NEBULA CAFE.",
                texts=["NEBULA CAFE"],
            )
        )
        assert understood is not None
        self.assertEqual(understood.texts, ["NEBULA CAFE"])

    async def test_only_the_current_texts_are_sent_to_the_painter(self) -> None:
        from services.orchestrator.pictures import followup

        gateway = DialogueGateway(
            follow_reply("change", "Replace the caption.", texts=["NEBULA CAFE"])
        )
        server = EditServer()
        first, picture_set = await self.start(gateway, server)
        picture_set.texts = ["OLD HARBOR"]
        picture_set.checklist = {"things": []}
        picture_set.current_source = '<svg xmlns="http://www.w3.org/2000/svg"></svg>'
        painter_messages, redrawn = followup.messages(
            picture_set, "Replace the caption.", ["NEBULA CAFE"]
        )
        self.assertFalse(redrawn)
        self.assertIn("NEBULA CAFE", json.dumps(painter_messages, ensure_ascii=False))
        self.assertNotIn("OLD HARBOR", json.dumps(painter_messages, ensure_ascii=False))

    async def test_contentless_follow_ups_carry_the_conversation(self) -> None:
        # Two languages, typos, no visual word in the last message: the
        # reading call gets the earlier critique and Atlas's own plan.
        cases = [
            "bon, applique",
            "vasy fonce",
            "et maintenant ?",
            "recommence en corigeant",
            "toujours pareil, rien n'a bougé",
            "pls fix it nd try agian",
            "go ahead with your plan",
            "well?",
        ]
        for text in cases:
            with self.subTest(text=text):
                gateway, server = DialogueGateway(), EditServer()
                first, picture_set = await self.start(gateway, server)
                history = [
                    ("user", "un jardinier en manteau vert"),
                    ("assistant", first.reponse),
                    ("user", "on ne voit pas sa tête"),
                    (
                        "assistant",
                        "Ce que je propose de changer : descendre le jardinier.",
                    ),
                ]
                gateway.reply = rewrite_reply(
                    prompt=FIRST.replace(
                        "waters", "is fully visible, head included, and waters"
                    )
                )
                done, item = await self.follow(gateway, history, text)
                self.assertTrue(done)
                sent = json.loads(gateway.follows[-1]["messages"][1]["content"])
                self.assertEqual(sent["last_message"], text)
                earlier = " ".join(t["text"] for t in sent["earlier_turns"])
                self.assertIn("on ne voit pas sa tête", earlier)
                self.assertIn("descendre le jardinier", earlier)
                self.assertNotIn("data:image", json.dumps(sent))
                # The understood change, not the bare words, reaches the rewrite.
                change = gateway.payloads[-1]["messages"][1]["content"]
                self.assertIn("Show the whole head.", change)
                self.assertIn(json.dumps(REQUIREMENTS), change)
                self.assertIn("Voici l'image modifiée", item.reponse)
                self.assertEqual(item.task_type, "drawing")

    async def test_other_messages_leave_the_picture_path(self) -> None:
        for kind in ("new", "other"):
            with self.subTest(kind=kind):
                gateway, server = DialogueGateway(follow_reply(kind)), FakeServer()
                first, _ = await self.start(gateway, server)
                history = [("user", "un jardinier"), ("assistant", first.reponse)]
                done, item = await self.follow(gateway, history, "merci, et la météo ?")
                self.assertFalse(done)
                self.assertEqual(len(server.renders), 2)  # the first two only
                self.assertEqual(item.cout_eur, 0)

    async def test_file_requests_export_the_picture_shown(self) -> None:
        cases = [
            ("fr", "donne le moulin en SVG s'il te plaît", "le-moulin.svg"),
            ("en", "can I get the teapot as a downloadable SVG?", "teapot.svg"),
            ("de", "exportiere das Drachenbild als kite.svg", "kite.svg"),
            ("es", "exporta la cometa como archivo SVG", "cometa.svg"),
        ]
        for language, text, filename in cases:
            with self.subTest(language=language, text=text):
                patcher = patch.dict(
                    os.environ,
                    {"ATLAS_PICTURES_BY_CODE": "1", "ATLAS_PICTURES_CPU": "0"},
                )
                patcher.start()
                self.addCleanup(patcher.stop)
                gateway = DialogueGateway(follow_reply("file"))
                server = FakeServer()
                first, _ = await self.start(gateway, server)
                picture_set = self.picture_set(first)
                picture_set.title = {
                    "fr": "Le moulin",
                    "en": "Teapot",
                    "de": "Kite",
                    "es": "Cometa",
                }[language]
                store.update(picture_set)
                from services.orchestrator import artifact_store

                current = artifact_store.read(
                    picture_set.artifact_id,
                    "owner@example.test",
                    picture_set.artifact_version,
                )
                expected = picture_turn._standalone_svg(current.content).decode("utf-8")
                history = [("user", FIRST), ("assistant", first.reponse)]
                renders = len(server.renders)
                done, item = await self.follow(gateway, history, text)
                self.assertTrue(done)
                self.assertEqual(len(server.renders), renders)
                self.assertIn(f"**{filename}**", item.reponse)
                self.assertIn("/download", item.reponse)
                after = self.picture_set(first)
                self.assertEqual(after.artifact_version, picture_set.artifact_version)
                panel = artifact_store.read(
                    picture_set.artifact_id,
                    "owner@example.test",
                    picture_set.artifact_version,
                )
                self.assertEqual(panel.content, current.content)
                self.assertEqual(panel.filename, current.filename)
                export_id = re.search(r"/artifacts/([^/]+)/versions/", item.reponse)
                assert export_id is not None
                self.assertNotEqual(export_id.group(1), picture_set.artifact_id)
                exported = artifact_store.read(
                    export_id.group(1), "owner@example.test", 1
                )
                self.assertEqual(exported.filename, filename)
                self.assertEqual(exported.content.decode("utf-8"), expected)
                self.assertIn(
                    'xmlns="http://www.w3.org/2000/svg"',
                    exported.content.decode("utf-8"),
                )

    async def test_an_export_does_not_break_a_later_visual_change(self) -> None:
        gateway = DialogueGateway(follow_reply("file"))
        server = EditServer()
        first, _ = await self.start(gateway, server)
        picture_set = self.picture_set(first)
        history = [("user", FIRST), ("assistant", first.reponse)]
        _, exported = await self.follow(gateway, history, "give it as moulin.png")
        self.assertEqual(exported.task_type, "drawing")
        self.assertIn("**moulin.png**", exported.reponse)
        before = self.picture_set(first)
        self.assertEqual(before.artifact_version, picture_set.artifact_version)

        gateway.follow = follow_reply("change", "Add a red windmill sail.")
        gateway.reply = rewrite_reply(prompt=FIRST + " Add a red windmill sail.")
        done, changed = await self.follow(
            gateway,
            history
            + [
                ("user", "give it as moulin.svg"),
                ("assistant", exported.reponse),
            ],
            "add a red windmill sail",
        )
        self.assertTrue(done)
        self.assertEqual(len(server.edits), 1)
        after = self.picture_set(first)
        self.assertEqual(after.artifact_version, picture_set.artifact_version + 1)
        from services.orchestrator import artifact_store

        panel = artifact_store.read(
            after.artifact_id, "owner@example.test", after.artifact_version
        )
        self.assertIn(b"<svg", panel.content)

    async def test_a_mixed_file_request_is_a_visual_change(self) -> None:
        gateway, server = (
            DialogueGateway(
                follow_reply(
                    "change",
                    "Show the fern with more fronds.",
                    requirements=["a fern", "more fronds"],
                )
            ),
            FakeServer(),
        )
        first, _ = await self.start(gateway, server)
        history = [("user", FIRST), ("assistant", first.reponse)]
        done, _ = await self.follow(
            gateway, history, "add more fronds to the fern and give it as SVG"
        )
        self.assertTrue(done)
        prompt = gateway.follows[-1]["messages"][0]["content"]
        self.assertIn(
            'a message that also asks for a visual change is "change"', prompt
        )
        self.assertIn(
            "A file format, a file name, a download link or an attachment is never "
            "a visual change or a requirement",
            prompt,
        )

    async def test_a_raster_picture_with_svg_asked_is_exported_as_png(self) -> None:
        gateway = DialogueGateway(follow_reply("file"))
        server = FakeServer()
        first, _ = await self.start(gateway, server)
        picture_set = self.picture_set(first)
        picture_set.title = "Windmill"
        picture_set.engine = "cpu"
        picture_set.by_code = False
        store.update(picture_set)
        history = [("user", FIRST), ("assistant", first.reponse)]
        done, item = await self.follow(gateway, history, "please give windmill.svg")
        self.assertTrue(done)
        self.assertRegex(
            item.reponse.lower(),
            r"raster image|image matricielle|mapa de bits",
        )
        self.assertIn("**windmill.png**", item.reponse)
        self.assertIn("/download", item.reponse)
        after = self.picture_set(first)
        self.assertEqual(after.artifact_version, picture_set.artifact_version)
        from services.orchestrator import artifact_store

        export_id = re.search(r"/artifacts/([^/]+)/versions/", item.reponse)
        assert export_id is not None
        self.assertNotEqual(export_id.group(1), picture_set.artifact_id)

        exported = artifact_store.read(export_id.group(1), "owner@example.test", 1)
        self.assertEqual(exported.filename, "windmill.png")
        self.assertEqual(
            exported.content,
            store.image_bytes(store.current_image_key(picture_set)),
        )

    async def test_a_raster_picture_is_exported_in_the_requested_format(self) -> None:
        for extension, expected_format in (("jpg", "JPEG"), ("webp", "WEBP")):
            with self.subTest(extension=extension):
                from services.orchestrator import image_store

                gateway = DialogueGateway(follow_reply("file"))
                server = FakeServer()
                first, _ = await self.start(gateway, server)
                picture_set = self.picture_set(first)
                picture_set.title = "Windmill"
                picture_set.engine = "cpu"
                picture_set.by_code = False
                picture_set.image_key = image_store.store(
                    "data:image/png;base64," + test_picture_cpu.png()
                ).rsplit("/", 1)[-1]
                store.update(picture_set)
                history = [("user", FIRST), ("assistant", first.reponse)]
                done, item = await self.follow(
                    gateway, history, f"please give windmill.{extension}"
                )
                self.assertTrue(done)
                self.assertEqual(item.task_type, "drawing")
                self.assertIn(f"**windmill.{extension}**", item.reponse)
                self.assertIn("/download", item.reponse)
                after = self.picture_set(first)
                self.assertEqual(after.artifact_version, picture_set.artifact_version)
                from services.orchestrator import artifact_store

                export_id = re.search(r"/artifacts/([^/]+)/versions/", item.reponse)
                assert export_id is not None
                exported = artifact_store.read(
                    export_id.group(1), "owner@example.test", 1
                )
                self.assertEqual(exported.filename, f"windmill.{extension}")
                with Image.open(io.BytesIO(exported.content)) as image:
                    self.assertEqual(image.format, expected_format)

    async def test_a_routed_drawing_follow_up_exports_the_picture_shown(self) -> None:
        from services.orchestrator.request_router import Decision
        from services.orchestrator import image_store

        gateway = DialogueGateway(follow_reply("file"))
        server = FakeServer()
        first, _ = await self.start(gateway, server)
        picture_set = self.picture_set(first)
        picture_set.title = "Windmill"
        picture_set.engine = "cpu"
        picture_set.by_code = False
        picture_set.image_key = image_store.store(
            "data:image/png;base64," + test_picture_cpu.png()
        ).rsplit("/", 1)[-1]
        store.update(picture_set)
        history = [("user", FIRST), ("assistant", first.reponse)]
        done, item = await self.follow(
            gateway,
            history,
            "the image file does not show the windmill; give it as windmill.jpg",
            routed=Decision(kind="drawing", file_format="jpg", follow_up=True),
        )
        self.assertTrue(done)
        self.assertEqual(item.task_type, "drawing")
        self.assertIn("**windmill.jpg**", item.reponse)
        self.assertIn("/download", item.reponse)
        after = self.picture_set(first)
        self.assertEqual(after.artifact_version, picture_set.artifact_version)
        from services.orchestrator import artifact_store

        export_id = re.search(r"/artifacts/([^/]+)/versions/", item.reponse)
        assert export_id is not None
        exported = artifact_store.read(export_id.group(1), "owner@example.test", 1)
        self.assertEqual(exported.filename, "windmill.jpg")
        with Image.open(io.BytesIO(exported.content)) as image:
            self.assertEqual(image.format, "JPEG")

    async def test_a_router_file_request_is_not_exported_before_reading(self) -> None:
        from services.orchestrator.request_router import Decision

        gateway = DialogueGateway(follow_reply("other"))
        server = FakeServer()
        first, _ = await self.start(gateway, server)
        history = [("user", FIRST), ("assistant", first.reponse)]
        done, item = await self.follow(
            gateway,
            history,
            "make a PDF report describing this poster",
            routed=Decision(kind="file", file_format="pdf"),
        )
        self.assertFalse(done)
        self.assertEqual(len(gateway.follows), 1)
        self.assertNotIn("/download", item.reponse)

    async def test_outside_kinds_skip_the_reading_call(self) -> None:
        from services.orchestrator.request_router import Decision

        gateway, server = DialogueGateway(), FakeServer()
        first, _ = await self.start(gateway, server)
        history = [("user", "un jardinier"), ("assistant", first.reponse)]
        done, _ = await self.follow(
            gateway,
            history,
            "make a spreadsheet of it",
            routed=Decision(kind="remote"),
        )
        self.assertFalse(done)
        self.assertEqual(gateway.follows, [])

    async def test_a_polite_closing_skips_the_reading_call(self) -> None:
        gateway, server = DialogueGateway(), FakeServer()
        first, _ = await self.start(gateway, server)
        history = [("user", "un jardinier"), ("assistant", first.reponse)]
        for text in ("merci, c'est parfait !", "love it 😍"):
            with self.subTest(text=text):
                # Even when a failed reading would have kept a change.
                done, item = await self.follow(gateway, history, text, legacy=True)
                self.assertFalse(done)
                self.assertEqual(item.cout_eur, 0)
        self.assertEqual(gateway.follows, [])
        self.assertEqual(len(server.renders), 2)  # the first two only

    async def test_a_failed_reading_keeps_the_former_change(self) -> None:
        gateway = DialogueGateway(GatewayError("provider_error", 502))
        first, _ = await self.start(gateway, FakeServer())
        history = [("user", "un jardinier"), ("assistant", first.reponse)]
        done, _ = await self.follow(gateway, history, "plus grand", legacy=False)
        self.assertFalse(done)
        gateway.reply = rewrite_reply(prompt="A single tall gardener waters tulips.")
        done, item = await self.follow(gateway, history, "plus grand", legacy=True)
        self.assertTrue(done)
        self.assertIn("plus grand", gateway.payloads[-1]["messages"][1]["content"])
        self.assertIn("picture_follow_up_provider_error", item.erreurs)

    async def test_a_quota_refusal_is_the_users(self) -> None:
        gateway = DialogueGateway(GatewayError("provider_quota", 402, "credit"))
        first, _ = await self.start(gateway, FakeServer())
        history = [("user", "un jardinier"), ("assistant", first.reponse)]
        with self.assertRaises(GatewayError):
            await self.follow(gateway, history, "vas-y")

    def test_picture_of_needs_a_recent_answer_and_the_owner(self) -> None:
        class Current:
            artifact = {"artifact_id": "e" * 32, "version": 1}

        self.assertIsNone(dialogue.picture_of(Request("vas-y"), Current()))
        self.assertIsNone(dialogue.picture_of(Request("vas-y"), object()))

    # (b) Never a change claimed that did not happen.

    async def test_a_plain_retry_is_a_new_draw_said_as_such(self) -> None:
        gateway = DialogueGateway(follow_reply("change", ""))
        server = FakeServer()
        first, picture_set = await self.start(gateway, server)
        history = [("user", "un jardinier"), ("assistant", first.reponse)]
        done, item = await self.follow(gateway, history, "encore")
        self.assertTrue(done)
        prompt, seed = server.renders[-1]
        self.assertEqual(prompt, picture_set.prompt)
        self.assertNotEqual(seed, picture_set.seed)
        self.assertIn("nouveau tirage", item.reponse)
        self.assertNotIn("Voici l'image modifiée", item.reponse)
        self.assertIn("picture_cpu_variation", item.erreurs)
        after = store.load(picture_set.set_id)
        assert after is not None
        self.assertEqual(after.seed, seed)  # the new draw is the current picture
        self.assertEqual(after.changes, [])

    async def test_an_unchanged_prompt_gets_one_stronger_rewrite(self) -> None:
        gateway = CheckingDialogueGateway(follow_reply("change", "Add a red hat."))
        server = FakeServer()
        first, picture_set = await self.start(gateway, server)
        history = [("user", "un jardinier"), ("assistant", first.reponse)]
        done, item = await self.follow(gateway, history, "ajoute un chapeau rouge")
        self.assertTrue(done)
        rewrites = [
            p for p in gateway.payloads if p["messages"][0]["content"] == rewrite.SYSTEM
        ]
        self.assertIn(rewrite.STRONGER, rewrites[-1]["messages"][1]["content"])
        self.assertNotIn(rewrite.STRONGER, rewrites[-2]["messages"][1]["content"])
        # Still unchanged and nothing to check: the edit contract says so
        # and draws nothing, never a new draw presented as the correction.
        self.assertEqual(len(server.renders), 2)
        self.assertIn("rien n'a été redessiné", item.reponse)
        self.assertIn("la version 1 reste telle quelle", item.reponse)
        self.assertIn("picture_cpu_not_applied", item.erreurs)
        self.assertEqual(item.images, [])

    async def test_an_identical_picture_makes_no_new_version(self) -> None:
        gateway = CheckingDialogueGateway(follow_reply("change", "Add a red hat."))
        server = EditServer(echo=True)
        first, picture_set = await self.start(gateway, server)
        history = [("user", "un jardinier"), ("assistant", first.reponse)]
        gateway.reply = rewrite_reply(prompt=FIRST + " He wears a red hat.")
        done, item = await self.follow(gateway, history, "ajoute un chapeau rouge")
        self.assertTrue(done)
        self.assertIn("une image identique", item.reponse)
        self.assertIn("picture_cpu_same_image", item.erreurs)
        self.assertEqual(item.images, [])
        after = store.load(picture_set.set_id)
        assert after is not None
        self.assertEqual(after.artifact_version, picture_set.artifact_version)

    async def test_real_changes_accumulate_and_keep_the_seed(self) -> None:
        # A server that cannot edit, its redraw accepted each time: the
        # changed description is drawn with the picture's own seed.
        gateway = DialogueGateway(follow_reply("change", "Show the whole head."))
        server = FakeServer()
        first, picture_set = await self.start(gateway, server)
        history = [("user", "un jardinier"), ("assistant", first.reponse)]
        gateway.reply = rewrite_reply(prompt=FIRST + " His whole head is visible.")
        self.accept_redraw(picture_set)
        done, item = await self.follow(gateway, history, "montre sa tête")
        self.assertTrue(done)
        self.assertEqual(server.renders[-1][1], picture_set.seed)
        self.assertIn("n'a pas été retouchée sur place", item.reponse)
        stored = store.load(picture_set.set_id)
        assert stored is not None
        self.assertFalse(stored.proposed_redraw)  # used once
        self.accept_redraw(stored)
        gateway.follow = follow_reply("change", "Add a nose, a mouth and eyes.")
        gateway.reply = rewrite_reply(prompt=FIRST + " Head, eyes, nose and mouth.")
        history += [("user", "montre sa tête"), ("assistant", item.reponse)]
        done, again = await self.follow(gateway, history, "ajoute nez bouche yeux")
        self.assertTrue(done)
        message = gateway.payloads[-1]["messages"][1]["content"]
        self.assertIn(
            'Changes already made, all still wanted: ["Show the whole head."]', message
        )
        self.assertIn("word for word", message)
        after = store.load(picture_set.set_id)
        assert after is not None
        self.assertEqual(
            after.changes, ["Show the whole head.", "Add a nose, a mouth and eyes."]
        )
        self.assertIn("/versions/3/open", again.reponse)

    # (b2) Corrections are state: failed statements, proposals, a new method.

    def accept_redraw(self, picture_set: store.PictureSet) -> None:
        """As after Atlas proposed a redraw the person accepted."""
        picture_set.proposed_redraw = True
        store.update(picture_set)

    def mark_missed(self, picture_set: store.PictureSet, missed: list[str]) -> None:
        picture_set.candidates[picture_set.artifact_candidate].unmet = missed
        store.update(picture_set)

    async def test_a_complaint_naming_no_change_corrects_what_the_check_missed(
        self,
    ) -> None:
        gateway = CheckingDialogueGateway(follow_reply("change", ""))
        server = FakeServer()
        first, picture_set = await self.start(gateway, server)
        self.mark_missed(picture_set, ["le jardinier tient deux arrosoirs"])
        history = [("user", "un jardinier"), ("assistant", first.reponse)]
        gateway.reply = rewrite_reply(
            prompt=FIRST, checks=["le jardinier tient deux arrosoirs"]
        )
        gateway.met = [
            '{"met": [false], "seen": ["un arrosoir"]}',
            '{"met": [false], "seen": ["aucun arrosoir"]}',
            '{"met": [true]}',
        ]
        done, item = await self.follow(gateway, history, "c'est encore raté")
        self.assertTrue(done)
        message = gateway.payloads_of(rewrite.SYSTEM)[-1]["messages"][1]["content"]
        self.assertIn(
            "Correct the picture so that it clearly shows: le jardinier tient deux "
            "arrosoirs",
            message,
        )
        self.assertNotIn(texts.text("fr", "cpu_variation"), item.reponse)
        self.assertIn("demande déjà", item.reponse)
        self.assertIn("j'ai fait 3 essais", item.reponse)
        after = store.load(picture_set.set_id)
        assert after is not None
        self.assertEqual(after.unmet, [])
        self.assertEqual(after.misses, 0)
        self.assertEqual(after.seed, server.renders[-1][1])

    async def test_a_correction_still_missed_says_the_gap_and_counts_a_miss(
        self,
    ) -> None:
        gateway = CheckingDialogueGateway(
            follow_reply("change", "The kite has exactly six tails.")
        )
        server = EditServer()
        first, picture_set = await self.start(gateway, server)
        history = [("user", "un cerf-volant"), ("assistant", first.reponse)]
        gateway.reply = rewrite_reply(
            prompt="A single kite with exactly six tails flies over a beach.",
            checks=["le cerf-volant a six queues"],
        )
        gateway.met = ['{"met": [false], "seen": ["deux queues"]}'] * 10
        with patch.object(picture_turn, "available", lambda: False):
            done, item = await self.follow(gateway, history, "il faut six queues")
        self.assertTrue(done)
        answer = item.reponse.replace("\xa0", " ")
        self.assertIn("(l'image montre : deux queues)", answer)
        self.assertIn("aucun de mes 6 essais", answer)
        self.assertIn("picture_cpu_unsatisfied", item.erreurs)
        after = store.load(picture_set.set_id)
        assert after is not None
        self.assertEqual(after.misses, 1)
        self.assertEqual(after.unmet, ["le cerf-volant a six queues"])

    async def test_a_verified_picture_is_never_replaced_by_a_failing_one(
        self,
    ) -> None:
        gateway = CheckingDialogueGateway(
            follow_reply("change", "Add a red hat with two feathers.")
        )
        server = EditServer()
        first, picture_set = await self.start(gateway, server)
        self.mark_missed(picture_set, [])
        history = [("user", "un jardinier"), ("assistant", first.reponse)]
        gateway.reply = rewrite_reply(
            prompt=FIRST + " He wears a red hat with two feathers.",
            checks=["le chapeau a deux plumes"],
        )
        gateway.met = ['{"met": [false], "seen": ["une plume"]}'] * 10
        with patch.object(picture_turn, "available", lambda: False):
            done, item = await self.follow(gateway, history, "mets lui un chapeau")
        self.assertTrue(done)
        self.assertIn("la version 1", item.reponse)
        self.assertIn("reste telle quelle", item.reponse)
        self.assertIn("picture_cpu_kept", item.erreurs)
        self.assertEqual(item.images, [])
        after = store.load(picture_set.set_id)
        assert after is not None
        self.assertEqual(after.artifact_version, 1)

    async def test_the_same_miss_again_changes_method(self) -> None:
        memoise_drafts(self)
        gateway = CheckingDialogueGateway(follow_reply("change", ""))
        server = FakeServer()
        first, picture_set = await self.start(gateway, server)
        self.mark_missed(picture_set, ["la table a huit pieds"])
        picture_set = store.load(picture_set.set_id) or picture_set
        picture_set.misses = 1
        store.update(picture_set)
        history = [("user", "une table"), ("assistant", first.reponse)]
        gateway.reply = rewrite_reply(prompt=FIRST, checks=["la table a huit pieds"])
        gateway.met = ['{"met": [true]}'] + ['{"met": [false]}'] * 3
        with patch.object(picture_turn, "soft_seconds", lambda: 30.0):
            done, item = await self.follow(gateway, history, "toujours pas")
        self.assertTrue(done)
        self.assertEqual(len(server.renders), 2)  # the image model is not asked again
        self.assertIn("j'ai changé de méthode", item.reponse)
        self.assertIn("picture_change_code_first", item.erreurs)
        after = store.load(picture_set.set_id)
        assert after is not None
        self.assertEqual(after.artifact_version, 2)
        self.assertEqual(after.misses, 0)
        # Drawn by code now: its painter source is what a later change edits.
        self.assertTrue(after.by_code)
        self.assertTrue(after.current_source)

    async def test_after_a_drawing_by_code_an_accepted_plan_edits_that_drawing(
        self,
    ) -> None:
        """Staging -187: a plan accepted after the method changed to drawing by
        code went back to the image model, which missed again, and nothing
        changed. The drawing by code is edited instead (fictional subjects)."""
        for subject, plan in (
            ("trois lanternes vertes", ["Space the lanterns evenly"]),
            ("cuatro cometas azules", ["Move the sun fully into the frame"]),
        ):
            with self.subTest(subject=subject):
                memoise_drafts(self)
                gateway = CheckingDialogueGateway(follow_reply("change", ""))
                server = FakeServer()
                first, picture_set = await self.start(gateway, server)
                self.mark_missed(picture_set, [subject])
                picture_set = store.load(picture_set.set_id) or picture_set
                picture_set.misses = 1
                store.update(picture_set)
                history = [("user", subject), ("assistant", first.reponse)]
                gateway.reply = rewrite_reply(prompt=FIRST, checks=[subject])
                gateway.met = ['{"met": [true]}'] + ['{"met": [false]}'] * 3
                with patch.object(picture_turn, "soft_seconds", lambda: 30.0):
                    done, second = await self.follow(gateway, history, "toujours pas")
                self.assertTrue(done)
                coded = store.load(picture_set.set_id)
                assert coded is not None and coded.by_code
                coded.proposed = list(plan)
                store.update(coded)
                renders = len(server.renders)
                gateway.follow = follow_reply("change", "")
                history += [("user", "toujours pas"), ("assistant", second.reponse)]
                done, accepted = await self.follow(gateway, history, "vas-y")
                self.assertTrue(done)
                after = store.load(picture_set.set_id)
                assert after is not None
                # No new image-model picture: the drawing was edited.
                self.assertEqual(len(server.renders), renders)
                self.assertEqual(after.artifact_version, 3)
                self.assertEqual(after.proposed, [])
                self.assertIn("/versions/3/open", accepted.reponse)

    async def test_an_accepted_proposal_is_applied_from_the_stored_plan(self) -> None:
        gateway = DialogueGateway(follow_reply("review"))
        server = EditServer()
        first, picture_set = await self.start(gateway, server)
        history = [("user", "un jardinier"), ("assistant", first.reponse)]
        done, review = await self.follow(gateway, history, "il y a un souci ?")
        self.assertTrue(done)
        stored = store.load(picture_set.set_id)
        assert stored is not None
        plan = ["Move the gardener down so the head is inside the picture"]
        self.assertEqual(stored.proposed, plan)
        # The reading call loses the plan (the acceptance read as a plain retry).
        gateway.follow = follow_reply("change", "")
        gateway.reply = rewrite_reply(prompt=FIRST + " The gardener stands lower.")
        history += [("user", "il y a un souci ?"), ("assistant", review.reponse)]
        done, item = await self.follow(gateway, history, "d'accord, fais-le")
        self.assertTrue(done)
        follow_data = json.loads(gateway.follows[-1]["messages"][1]["content"])
        self.assertEqual(follow_data["proposed_change"], plan)
        message = gateway.payloads[-1]["messages"][1]["content"]
        self.assertIn("The user now asks for this change: " + plan[0], message)
        self.assertIn("Voici l'image modifiée", item.reponse)
        after = store.load(picture_set.set_id)
        assert after is not None
        self.assertEqual(after.proposed, [])
        self.assertEqual(after.changes, plan)

    # (c) A server that edits changes the picture itself.

    async def test_an_editing_server_edits_the_current_picture(self) -> None:
        gateway = DialogueGateway(follow_reply("change", "Show the whole head."))
        server = EditServer()
        first, picture_set = await self.start(gateway, server)
        history = [("user", "un jardinier"), ("assistant", first.reponse)]
        gateway.reply = rewrite_reply(
            prompt=FIRST + " His whole head is visible.",
            edit="Show the gardener's whole head.",
        )
        done, item = await self.follow(gateway, history, "on veut voir sa tête")
        self.assertTrue(done)
        prompt, seed, reference = server.edits[-1]
        self.assertTrue(prompt.startswith("Show the gardener's whole head. "))
        self.assertIn(cpu_turn.EDIT_KEEP, prompt)
        key, digest = store.current_render(picture_set)
        self.assertEqual(base64.b64decode(reference), store.image_bytes(key))
        self.assertEqual(cpu_turn.render_hash(reference), digest)
        self.assertEqual(len(server.renders), 2)  # no text-to-image redraw
        self.assertIn("Voici l'image modifiée", item.reponse)
        after = store.load(picture_set.set_id)
        assert after is not None
        self.assertTrue(after.edited)
        self.assertNotEqual(after.render_key, key)
        # The next edit starts from the edited picture.
        history += [("user", "on veut voir sa tête"), ("assistant", item.reponse)]
        gateway.reply = rewrite_reply(prompt=FIRST + " Red hat.", edit="Add a red hat.")
        await self.follow(gateway, history, "un chapeau rouge")
        self.assertEqual(
            base64.b64decode(server.edits[-1][2]), store.image_bytes(after.render_key)
        )

    async def test_a_failed_edit_keeps_the_picture_and_proposes_a_redraw(self) -> None:
        gateway = CheckingDialogueGateway(follow_reply("change", "Add a red hat."))
        server = EditServer(fail_edit=True)
        first, picture_set = await self.start(gateway, server)
        history = [("user", "un jardinier"), ("assistant", first.reponse)]
        gateway.reply = rewrite_reply(prompt=FIRST + " Red hat.", edit="Add a red hat.")
        done, item = await self.follow(gateway, history, "chapeau rouge")
        self.assertTrue(done)
        self.assertIn("picture_cpu_edit_failed", item.erreurs)
        self.assertIn("picture_cpu_no_edit_failed", item.erreurs)
        # Nothing drawn from scratch in its place: the two first pictures only.
        self.assertEqual(len(server.renders), 2)
        self.assertEqual(item.images, [])
        self.assertIn("n'a pas réussi à retoucher", item.reponse)
        self.assertIn("la version 1 reste telle quelle", item.reponse)
        stored = store.load(picture_set.set_id)
        assert stored is not None
        self.assertEqual(stored.artifact_version, 1)
        self.assertEqual(stored.proposed, ["Add a red hat."])
        self.assertTrue(stored.proposed_redraw)
        # Accepted: the changed description is drawn, and said as such.
        gateway.follow = follow_reply("change", "")
        history += [("user", "chapeau rouge"), ("assistant", item.reponse)]
        done, again = await self.follow(gateway, history, "oui vas-y")
        self.assertTrue(done)
        self.assertIn("Red hat.", server.renders[-1][0])
        self.assertIn("n'a pas été retouchée sur place", again.reponse)
        self.assertIn("/versions/2/open", again.reponse)

    async def test_a_busy_accepted_redraw_is_painted_by_code(self) -> None:
        gateway = CheckingDialogueGateway(follow_reply("change", "Add a red hat."))
        server = FakeServer()
        first, picture_set = await self.start(gateway, server)
        history = [("user", "un jardinier"), ("assistant", first.reponse)]
        gateway.reply = rewrite_reply(
            prompt=FIRST + " He wears a red hat.", checks=["le chapeau est rouge"]
        )
        gateway.met = ['{"met": [true, true, true], "seen": ["", "", ""]}']
        self.accept_redraw(picture_set)
        server.job_error = "picture_cpu_busy"
        done, item = await self.follow(gateway, history, "chapeau rouge")
        self.assertTrue(done)
        self.assertIn("picture_cpu_busy", item.erreurs)
        self.assertIn("picture_cpu_fallback", item.erreurs)
        self.assertIn("/versions/2/open", item.reponse)
        stored = store.load(picture_set.set_id)
        assert stored is not None
        self.assertEqual(stored.artifact_version, 2)
        self.assertTrue(stored.by_code)
        self.assertEqual(stored.changes, ["Add a red hat."])

    # (d) A review from evidence, never a promise.

    async def test_a_review_looks_at_the_picture_and_promises_nothing(self) -> None:
        for text in (
            "qu'est-ce qui cloche sur ce dessin ?",
            "rate this picture honestly plz",
            "pourquoi le décor a bougé ?",
        ):
            with self.subTest(text=text):
                gateway = DialogueGateway(follow_reply("review"))
                server = FakeServer()
                first, picture_set = await self.start(gateway, server)
                history = [("user", "un jardinier"), ("assistant", first.reponse)]
                done, item = await self.follow(gateway, history, text)
                self.assertTrue(done)
                self.assertEqual(len(server.renders), 2)  # nothing drawn
                payload = gateway.reviews[-1]
                self.assertEqual(payload["vision_role"], "locate")
                parts = payload["messages"][1]["content"]
                image = next(p for p in parts if p["type"] == "image_url")
                key = store.current_image_key(picture_set)
                raw = store.image_bytes(key)
                assert raw is not None
                self.assertEqual(
                    base64.b64decode(image["image_url"]["url"].split(",", 1)[1]), raw
                )
                data = json.loads(next(p for p in parts if p["type"] == "text")["text"])
                self.assertEqual(data["requirements"], REQUIREMENTS)
                self.assertIn("J'ai regardé l'image actuelle (version 1)", item.reponse)
                self.assertIn("the head is cut at the top", item.reponse)
                self.assertIn("Move the gardener down", item.reponse)
                self.assertTrue(item.reponse.endswith(texts.text("fr", "review_next")))
                self.assertIsNone(re.search(r"(?i)je (la )?g[ée]n[èe]re", item.reponse))
                self.assertEqual(item.images, [])

    async def test_a_review_without_the_vision_call_says_so(self) -> None:
        gateway = DialogueGateway(
            follow_reply("review"), GatewayError("provider_error", 502)
        )
        first, _ = await self.start(gateway, FakeServer())
        history = [("user", "un jardinier"), ("assistant", first.reponse)]
        done, item = await self.follow(gateway, history, "critique-la")
        self.assertTrue(done)
        self.assertIn("Je n'ai pas pu regarder l'image", item.reponse)
        self.assertNotIn("Elle montre", item.reponse)
        self.assertIn("a green coat", item.reponse)  # what was asked, from the record
        self.assertIn("picture_review_provider_error", item.erreurs)

    def test_review_answer_never_claims_unseen_content(self) -> None:
        picture_set = store.PictureSet("a" * 32, "o", "en", "t", [], request="r")
        value = {"shown": ["a hat"], "missing": [], "plan": ["Add a dog"]}
        unseen = dialogue.review_answer("en", picture_set, value, False, ["a hat"])
        self.assertNotIn("It shows", unseen)
        self.assertIn("could not look", unseen)
        self.assertIn("Add a dog", unseen)
        seen = dialogue.review_answer("en", picture_set, value, True, ["a hat"])
        self.assertIn("It shows: a hat.", seen)

    # (e) The latest version, never the HTML artifact editor.

    async def test_an_older_version_in_the_chat_changes_the_latest(self) -> None:
        gateway = DialogueGateway(follow_reply("change", "Add a red hat."))
        server = EditServer()
        first, picture_set = await self.start(gateway, server)
        gateway.reply = rewrite_reply(prompt=FIRST + " Red hat.")
        changed = Interaction()
        self.assertTrue(
            await process_picture_followup(
                Request("chapeau rouge"),
                changed,
                self.current(first.reponse),
                complete=gateway,
                instruction="Add a red hat.",
            )
        )
        self.assertIn("/versions/2/open", changed.reponse)
        # The chat still shows version 1 (a reply regenerated): version 3 follows 2.
        gateway.reply = rewrite_reply(prompt=FIRST + " Red hat. Blue boots.")
        again = Interaction()
        self.assertTrue(
            await process_picture_followup(
                Request("bottes bleues"),
                again,
                self.current(first.reponse),
                complete=gateway,
                instruction="Add blue boots.",
            )
        )
        self.assertIn("/versions/3/open", again.reponse)
        self.assertNotIn("artifact_conflict", again.erreurs)

    async def test_pipeline_never_sends_a_picture_to_the_artifact_editor(self) -> None:
        from services.orchestrator import chat_pipeline
        from services.orchestrator.request_router import DECISION_KEY, Decision
        from test_request_router import request as chat_request

        gateway, server = DialogueGateway(), FakeServer()
        first, _ = await self.start(gateway, server)
        history = [
            {"role": "user", "content": "un jardinier"},
            {"role": "assistant", "content": first.reponse},
        ]
        for kind, follow, expect_picture in (
            ("drawing", follow_reply("change", "Show the whole head."), True),
            ("app", follow_reply("review"), True),
            ("app", follow_reply("other"), False),
        ):
            with self.subTest(kind=kind, follow=follow):
                req = chat_request("alors ?", history)
                req.__dict__["artifact_owner"] = "owner@example.test"
                req.__dict__[DECISION_KEY] = Decision.model_validate(
                    {"kind": kind, "follow_up": True}
                )
                gateway.follow = follow
                gateway.reply = rewrite_reply(prompt=FIRST + " Whole head.")
                app_turn = AsyncMock()
                item = Interaction()
                with (
                    patch.dict(os.environ, {"ATLAS_MODEL_ROUTER": "1"}),
                    patch.object(chat_pipeline, "process_app_turn", app_turn),
                    patch("services.orchestrator.pictures.turn._gateway", gateway),
                ):
                    await chat_pipeline._process(req, item)
                if expect_picture:
                    app_turn.assert_not_awaited()
                    self.assertEqual(item.task_type, "drawing")
                else:
                    # A new app, never the picture as the artifact to edit.
                    app_turn.assert_awaited_once()
                    called = app_turn.await_args
                    assert called is not None
                    self.assertEqual(called.kwargs["intent"], "create")
                    self.assertIsNone(called.kwargs["current"])


class CodeEngineIdenticalTests(Base):
    """A drawing by code that the painter gives back unchanged to a correction.

    Staging -185: after a picture drawn by code (the image model had failed
    its check), a complaint and then an acceptance were both answered "the
    drawing came back identical, tell me precisely what should change": the
    person had already said it. The method now changes once (drawn again from
    the first request with the correction) before saying nothing changed.
    """

    async def changed_after(
        self,
        request: str,
        correction: str,
        *,
        same_on_redraw: bool,
        wall_clock_limit: float | None = None,
    ) -> tuple[Interaction, store.PictureSet, Any]:
        from test_picture_turn import FakeGateway

        from services.orchestrator.pictures import turn

        class Same(FakeGateway):
            last = ""
            edits = 0
            redraws = 0

            async def __call__(
                self, payload: dict[str, Any], seconds: float
            ) -> dict[str, Any]:
                reply = await super().__call__(payload, seconds)
                asked = str(payload["messages"][-1]["content"])
                if "Change the picture as asked" in asked:
                    self.edits += 1
                    reply["text"] = "```svg\n" + self.last + "\n```"
                elif "Also apply this change" in asked:
                    self.redraws += 1
                    if same_on_redraw:
                        reply["text"] = "```svg\n" + self.last + "\n```"
                return reply

        with patch.dict(os.environ, {"ATLAS_PICTURES_CPU": ""}):
            gateway = Same("cats")
            item = Interaction()
            await turn.process_picture_turn(Request(request), item, complete=gateway)
            picture_set = self.picture_set(item)
            gateway.last = picture_set.current_source
            changed = Interaction()
            done = await process_picture_followup(
                Request("c'est pas ça"),
                changed,
                current(item.reponse),
                complete=gateway,
                instruction=correction,
                wall_clock_limit=wall_clock_limit,
            )
        self.assertTrue(done)
        return changed, picture_set, gateway

    async def test_an_identical_edit_is_drawn_again_from_the_request(self) -> None:
        for request, correction in (
            ("trois chats sur un muret", "Show three cats; the picture shows two."),
            ("un chat avec deux nœuds rouges", "Give the cat two red bows, not one."),
        ):
            with self.subTest(request=request):
                changed, picture_set, gateway = await self.changed_after(
                    request, correction, same_on_redraw=False
                )
                after = store.load(picture_set.set_id)
                assert after is not None
                self.assertEqual(
                    after.artifact_version, picture_set.artifact_version + 1
                )
                self.assertEqual((gateway.edits, gateway.redraws), (1, 1))
                redraw = gateway.payloads[-1]["messages"]
                # Drawn again from the request with the correction, never from
                # the drawing that kept the fault.
                self.assertIn(correction, str(redraw[-1]["content"]))
                self.assertNotIn(
                    picture_set.current_source,
                    [str(m["content"]) for m in redraw],
                )
                self.assertIn("changé de méthode", changed.reponse)
                self.assertIn("/versions/2/open", changed.reponse)

    async def test_a_passing_provider_failure_is_tried_again(self) -> None:
        from test_picture_turn import FakeGateway

        from services.orchestrator import app_turn
        from services.orchestrator.pictures import followup, turn

        for code in ("provider_error", "incomplete_stream"):
            with self.subTest(code=code):

                class CutOnce(FakeGateway):
                    cuts = 0

                    async def __call__(
                        self, payload: dict[str, Any], seconds: float
                    ) -> dict[str, Any]:
                        asked = str(payload["messages"][-1]["content"])
                        if "Change the picture as asked" in asked and not self.cuts:
                            self.cuts += 1
                            raise GatewayError(code, 502)
                        return await super().__call__(payload, seconds)

                with (
                    patch.dict(os.environ, {"ATLAS_PICTURES_CPU": ""}),
                    patch.object(followup, "TRANSIENT_WAITS", (0.0, 0.0, 0.0)),
                ):
                    self.assertEqual(len(app_turn.TRANSIENT_WAITS), 3)
                    gateway = CutOnce("cats")
                    item = Interaction()
                    await turn.process_picture_turn(
                        Request("deux chats sur un banc"), item, complete=gateway
                    )
                    picture_set = self.picture_set(item)
                    changed = Interaction()
                    await process_picture_followup(
                        Request("c'est pas ça"),
                        changed,
                        current(item.reponse),
                        complete=gateway,
                        instruction="Give the bench a green colour.",
                    )
                self.assertEqual(gateway.cuts, 1)
                self.assertNotIn("picture_change_" + code, changed.erreurs)
                self.assertNotIn("Renvoie ton message", changed.reponse)
                after = store.load(picture_set.set_id)
                assert after is not None
                self.assertEqual(
                    after.artifact_version, picture_set.artifact_version + 1
                )

    async def test_a_lasting_provider_failure_keeps_the_version(self) -> None:
        from test_picture_turn import FakeGateway

        from services.orchestrator.pictures import followup, turn

        class AlwaysCut(FakeGateway):
            cuts = 0

            async def __call__(
                self, payload: dict[str, Any], seconds: float
            ) -> dict[str, Any]:
                asked = str(payload["messages"][-1]["content"])
                if "Change the picture as asked" in asked:
                    self.cuts += 1
                    raise GatewayError("provider_error", 502)
                return await super().__call__(payload, seconds)

        with (
            patch.dict(os.environ, {"ATLAS_PICTURES_CPU": ""}),
            patch.object(followup, "TRANSIENT_WAITS", (0.0, 0.0, 0.0)),
        ):
            gateway = AlwaysCut("cats")
            item = Interaction()
            await turn.process_picture_turn(
                Request("un phare sous la pluie"), item, complete=gateway
            )
            picture_set = self.picture_set(item)
            changed = Interaction()
            await process_picture_followup(
                Request("c'est pas ça"),
                changed,
                current(item.reponse),
                complete=gateway,
                instruction="Make the lighthouse red.",
            )
        # The first call and three retries, then the version stays.
        self.assertEqual(gateway.cuts, 4)
        self.assertIn("picture_change_provider_error", changed.erreurs)
        after = store.load(picture_set.set_id)
        assert after is not None
        self.assertEqual(after.artifact_version, picture_set.artifact_version)

    async def test_identical_twice_keeps_the_version_and_asks_nothing_again(
        self,
    ) -> None:
        changed, picture_set, gateway = await self.changed_after(
            "cinq chats endormis",
            "Show five cats; four are drawn.",
            same_on_redraw=True,
        )
        after = store.load(picture_set.set_id)
        assert after is not None
        self.assertEqual(after.artifact_version, picture_set.artifact_version)
        self.assertIn("picture_change_identical", changed.erreurs)
        self.assertEqual((gateway.edits, gateway.redraws), (1, 1))
        self.assertIn("Rien n'a été redessiné", changed.reponse)
        self.assertNotIn("précisément ce qui doit changer", changed.reponse)
        self.assertNotIn("/versions/2/", changed.reponse)

    async def test_without_time_for_a_redraw_the_version_stays(self) -> None:
        changed, picture_set, gateway = await self.changed_after(
            "un chat sur un tabouret",
            "Put the cat on a stool.",
            same_on_redraw=False,
            wall_clock_limit=80.0,
        )
        after = store.load(picture_set.set_id)
        assert after is not None
        self.assertEqual(after.artifact_version, picture_set.artifact_version)
        self.assertEqual((gateway.edits, gateway.redraws), (1, 0))
        self.assertIn("identique", changed.reponse)
        self.assertNotIn("modifiée", changed.reponse)


class GateEditTests(unittest.TestCase):
    def test_gate_checks_and_resizes_the_reference(self) -> None:
        from test_picture_cpu_gate import gate

        reference = gate.reference_picture(png(size=300))
        picture = Image.open(io.BytesIO(base64.b64decode(reference)))
        self.assertEqual(picture.size, (gate.DRAW_PX, gate.DRAW_PX))
        for bad in ("bm90IGFuIGltYWdl", "***", png(size=8)):
            with self.subTest(bad=bad[:10]):
                with self.assertRaises(ValueError):
                    gate.reference_picture(bad)
        body = gate.engine_body("Make the scarf red.", 7, reference)
        self.assertEqual(body["extra_images"], [reference])
        self.assertNotIn("extra_images", gate.engine_body("A fox.", 7))
        self.assertTrue(gate.MODELS["klein"]["edit"])
        self.assertFalse(gate.MODELS["zimage"].get("edit", False))

    def test_gate_queue_counts_edits_at_their_own_time(self) -> None:
        from test_picture_cpu_gate import Clock, gate

        queue = gate.Queue(per_image=20.0, max_images=8, clock=Clock(), per_edit=50.0)
        job, expected = queue.admit(1, 100.0, edit=True)
        self.assertIsNotNone(job)
        self.assertEqual(expected, 50.0)
        _, expected = queue.admit(2, 200.0)
        self.assertEqual(expected, 90.0)
        none, expected = queue.admit(1, 120.0, edit=True)
        self.assertIsNone(none)  # 140 s would not fit
        queue.wait_turn(job)
        queue.start_image(edit=True)
        queue.image_done(job, 60.0)
        self.assertEqual(queue.per_edit, 52.0)
        self.assertEqual(queue.per_image, 20.0)

    def test_client_expected_wait_for_an_edit(self) -> None:
        status = {"seconds_per_image": 20.0, "busy_seconds": 10.0}
        self.assertEqual(cpu_server.expected_wait(status, 1), 30.0)
        self.assertEqual(cpu_server.expected_wait(status, 1, edit=True), 54.0)
        status["seconds_per_edit"] = 48.0
        self.assertEqual(cpu_server.expected_wait(status, 1, edit=True), 58.0)
        self.assertFalse(cpu_server.edits(status))
        self.assertTrue(cpu_server.edits(status | {"edit": True}))


class CodeEditVerifiedTests(Base):
    """A correction drawn by code is checked before it is shown (docs/32):
    retried with the measured gap, the closest shown with its gap, or the
    verified version kept. Fictional subjects and statements."""

    async def corrected(
        self,
        replies: list[tuple[list[bool], list[str]]],
        requirements: list[str],
        *,
        current_passed: bool = False,
        own_checks: list[str] | None = None,
    ) -> tuple[Interaction, store.PictureSet, Any]:
        from test_picture_turn import FakeGateway

        from services.orchestrator.pictures import turn

        class Checked(FakeGateway):
            checks: list[list[str]]
            edits: list[str]

            async def __call__(
                self, payload: dict[str, Any], seconds: float
            ) -> dict[str, Any]:
                system = str(payload["messages"][0]["content"])
                if "You check a picture against statements" in system:
                    asked = payload["messages"][1]["content"][0]["text"]
                    self.checks.append(json.loads(asked.split(": ", 1)[1]))
                    met, seen = replies[min(len(self.checks), len(replies)) - 1]
                    return {
                        "text": json.dumps({"met": met, "seen": seen}),
                        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                        "cost_eur": "0.0001",
                    }
                asked = str(payload["messages"][-1]["content"])
                if "Change the picture as asked" in asked:
                    self.edits.append(asked)
                return await super().__call__(payload, seconds)

        with patch.dict(os.environ, {"ATLAS_PICTURES_CPU": ""}):
            gateway = Checked("cats")
            gateway.checks, gateway.edits = [], []
            item = Interaction()
            await turn.process_picture_turn(
                Request("des chats sur un muret"), item, complete=gateway
            )
            picture_set = self.picture_set(item)
            gateway.checks, gateway.edits = [], []
            if current_passed:
                picture_set.unmet = []
            if own_checks:
                picture_set.checks = list(own_checks)
            store.update(picture_set)
            changed = Interaction()
            done = await process_picture_followup(
                Request("il en manque un"),
                changed,
                current(item.reponse),
                complete=gateway,
                instruction="Show one more cat on the wall.",
                requirements=requirements,
            )
        self.assertTrue(done)
        return changed, picture_set, gateway

    async def test_a_drawing_that_passes_is_shown(self) -> None:
        changed, picture_set, gateway = await self.corrected(
            [([True], [""])], ["trois chats sur le muret"]
        )
        after = store.load(picture_set.set_id)
        assert after is not None
        self.assertEqual(after.artifact_version, picture_set.artifact_version + 1)
        self.assertEqual(gateway.checks, [["trois chats sur le muret"]])
        self.assertEqual(after.unmet, [])
        self.assertIn("/versions/2/open", changed.reponse)

    async def test_a_miss_is_drawn_again_with_the_measured_gap(self) -> None:
        changed, picture_set, gateway = await self.corrected(
            [([False, True], ["dos gatos", ""]), ([True, True], ["", ""])],
            ["tres gatos en el muro", "un farol encendido"],
        )
        self.assertEqual(len(gateway.edits), 2)
        self.assertIn("does not show: tres gatos en el muro", gateway.edits[1])
        self.assertIn("dos gatos", gateway.edits[1])
        self.assertIn("J'ai fait 2 dessins", changed.reponse)
        after = store.load(picture_set.set_id)
        assert after is not None
        self.assertEqual(after.artifact_version, picture_set.artifact_version + 1)

    async def test_every_miss_shows_the_closest_with_its_gap(self) -> None:
        changed, picture_set, gateway = await self.corrected(
            [([False], ["zwei Katzen"])], ["drei Katzen auf der Mauer"]
        )
        self.assertEqual(len(gateway.checks), 3)
        self.assertIn("n'a pas réussi à montrer", changed.reponse)
        self.assertIn("zwei Katzen", changed.reponse)
        self.assertIn("picture_change_unsatisfied", changed.erreurs)
        after = store.load(picture_set.set_id)
        assert after is not None
        self.assertEqual(after.unmet, ["drei Katzen auf der Mauer"])
        self.assertEqual(after.misses, 1)

    async def test_a_verified_version_is_never_replaced_by_a_miss(self) -> None:
        changed, picture_set, gateway = await self.corrected(
            [([False], ["deux chats"])],
            ["trois chats sur le muret"],
            current_passed=True,
        )
        after = store.load(picture_set.set_id)
        assert after is not None
        self.assertEqual(after.artifact_version, picture_set.artifact_version)
        self.assertIn("Mes 3 nouveaux dessins", changed.reponse)
        self.assertNotIn("/versions/2/", changed.reponse)

    async def test_the_answer_quotes_the_statement_in_the_persons_language(
        self,
    ) -> None:
        for language, _, own, english, lead in QuotedStatementsLanguageTests.CASES:
            with self.subTest(language=language):
                changed, _, gateway = await self.corrected(
                    [([False], ["deux"])],
                    [english],
                    current_passed=True,
                    own_checks=[own],
                )
                self.assertEqual(gateway.checks[0], [own])
                self.assertIn(own, changed.reponse)
                self.assertNotIn(english, changed.reponse)

    async def test_an_unchecked_drawing_is_said_unverified(self) -> None:
        changed, picture_set, gateway = await self.corrected(
            [([True, True], [""])], ["trois chats sur le muret"]
        )
        # The reply had two values for one statement: the check is unread.
        self.assertIn("n'a pas pu être vérifié", changed.reponse)
        self.assertIn("picture_change_unchecked", changed.erreurs)


class QuotedStatementsLanguageTests(Base):
    """What a picture answer quotes is in the person's language (staging -191:
    a French conversation quoted an English requirement). Fictional texts."""

    CASES = (
        (
            "fr",
            "des chats sur un muret",
            "Le chat a deux nœuds rouges",
            "The cat has two red bows",
            "Mes 3 nouveaux dessins",
        ),
        (
            "de",
            "Katzen auf einer Mauer",
            "Die Katze trägt zwei rote Schleifen",
            "The cat has two red bows",
            "Meine 3 neuen Zeichnungen",
        ),
        (
            "es",
            "gatos en un muro",
            "El gato lleva dos lazos rojos",
            "The cat has two red bows",
            "Mis 3 nuevos dibujos",
        ),
        (
            "it",
            "gatti su un muretto",
            "Il gatto ha due fiocchi rossi",
            "The cat has two red bows",
            "I miei 3 nuovi disegni",
        ),
    )

    def test_the_pictures_own_statements_win_over_english_requirements(self) -> None:
        from services.orchestrator.pictures import followup

        for language, _, own, english, _ in self.CASES:
            with self.subTest(language=language):
                picture_set = store.PictureSet(
                    set_id="x", owner="o", language=language, title="t", candidates=[]
                )
                picture_set.checks = [own]
                found = followup.statements_of(picture_set, [english], "Add bows.")
                self.assertEqual(found, [own])
                picture_set.checks = []
                self.assertEqual(
                    followup.statements_of(picture_set, [english], "Add bows."),
                    [english],
                )
                self.assertEqual(
                    followup.statements_of(picture_set, None, "Add bows."),
                    ["Add bows."],
                )

    def test_the_reading_call_writes_requirements_in_the_users_language(self) -> None:
        system = dialogue.FOLLOW_SYSTEM
        self.assertIn("in the language of the user's own words", system)
        self.assertNotIn("every later correction, in English", system)
