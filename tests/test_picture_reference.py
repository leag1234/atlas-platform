"""A change that names a picture already shown edits that picture.

"In the second one, make the sky orange", "keep image 2 but ...", "la
dernière, sans le bateau": the change applies to the picture named (by its
number, an ordinal or "the last"), from its stored original, and changes
only what was asked; never a new set drawn from scratch, never another
picture. When the image server cannot edit, the picture is kept, the reason
is said and the closest option is proposed. Fictional subjects only.
"""

from __future__ import annotations

import base64
import json
import unittest
from unittest.mock import AsyncMock, patch

from services.orchestrator.interactions import Interaction
from services.orchestrator.pictures import (
    choice,
    cpu_server,
    cpu_turn,
    dialogue,
    followup,
    painter,
    reference,
    store,
)
from services.orchestrator.pictures import turn as picture_turn
from test_picture_cpu import FakeServer, rewrite_reply
from test_picture_followups import (
    Base,
    CheckingDialogueGateway,
    DialogueGateway,
    EditServer,
    follow_reply,
)
from test_picture_turn import Request

HERON = "A grey heron stands on a wooden pier by a calm lake under a pale sky."
OWNER = "owner@example.test"
LAST = reference.LAST


class ReferenceWordsTests(unittest.TestCase):
    NAMED = {
        "in the second one, make the sky orange": 2,
        "keep the second image but make the sky orange": 2,
        "picture 3: remove the hat": 3,
        "change only the boat in pic #1": 1,
        "the last one, but with a moon": LAST,
        "2nd pic: sky orange plz": 2,
        "dans la deuxième, rends le ciel orange": 2,
        "sur la première image, enlève seulement le chapeau": 1,
        "garde l'image 2 mais mets un ciel orange": 2,
        "la dernière, sans le bateau": LAST,
        "proposition n°2 avec un ciel orange stp": 2,
        "dans la 2e, ciel orange": 2,
        "auf dem zweiten Bild nur den Himmel orange machen": 2,
        "Im ersten, entferne den Hut": 1,
        "Bild Nr. 2 behalten, aber mit Mond": 2,
        "en la segunda imagen, cambia solo el cielo": 2,
        "la primera pero sin sombrero": 1,
        "nella seconda immagine cambia solo il cielo": 2,
        "la terza, ma col cielo arancione": 3,
        "na segunda imagem, muda só o céu para laranja": 2,
        "a primeira, mas sem o chapéu": 1,
    }
    NOT_NAMED = (
        "make the second cat bigger",
        "First, make the sky orange",
        "une image très colorée avec un phare",
        "mets le phare au premier plan",
        "la deuxième fois, c'était mieux",
        "go back to version 2",
        "combine the first and the third picture",
        "dessine un chat avec deux chapeaux",
        "same drawing, remove the hat",
        "draw me a picture of two foxes",
        "zeichne das Bild ohne den zweiten Baum",
        "",
    )

    def test_a_picture_named_in_six_languages(self) -> None:
        for message, number in self.NAMED.items():
            with self.subTest(message=message):
                found = reference.referenced(message)
                self.assertIsNotNone(found)
                assert found is not None
                self.assertEqual(found.number, number)

    def test_counted_things_and_sequence_words_name_no_picture(self) -> None:
        for message in self.NOT_NAMED:
            with self.subTest(message=message):
                self.assertIsNone(reference.referenced(message))


class NamedPictureTests(Base):
    """A set of two CPU pictures, then a change that names one of them."""

    def serve(self, server: FakeServer) -> None:
        super().serve(server)
        if isinstance(server, EditServer):
            patcher = patch.object(cpu_server, "render_edit", server.render_edit)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def start(
        self, gateway: DialogueGateway, server: FakeServer, text: str = "un héron"
    ) -> tuple[Interaction, store.PictureSet]:
        gateway.reply = rewrite_reply(prompt=HERON)
        first = await self.run_turn(gateway, server, text)
        return first, self.picture_set(first)

    async def follow(
        self,
        gateway: DialogueGateway,
        history: list[tuple[str, str]],
        text: str,
    ) -> tuple[bool, Interaction, Request]:
        """As the pipeline: the named picture made current, then the follow-up."""
        from services.orchestrator.artifact_tools import current_artifact_message

        request = Request(text, history)
        messages: list[dict[str, object]] = [
            {"role": r, "content": t} for r, t in history
        ]
        messages.append({"role": "user", "content": text})
        item = Interaction()
        found = current_artifact_message(messages, OWNER)
        found, answered = await reference.handle_reference(
            request, item, found, item.request_id
        )
        if answered:
            return True, item, request
        picture_set = dialogue.picture_of(request, found)
        assert picture_set is not None
        done = await dialogue.follow_up(
            request,
            item,
            found,
            picture_set,
            None,
            legacy_change=False,
            complete=gateway,
        )
        return done, item, request

    async def test_the_named_candidate_is_edited_from_its_own_original(self) -> None:
        phrasings = (
            "dans la deuxième, rends seulement le ciel orange",
            "in the second one, change only the sky to orange",
            "auf dem zweiten Bild nur den Himmel orange",
            "en la segunda, solo el cielo naranja",
            "nella seconda immagine solo il cielo arancione",
            "na segunda imagem, só o céu laranja",
        )
        for text in phrasings:
            with self.subTest(text=text):
                gateway = DialogueGateway(
                    follow_reply("change", "Make the sky orange.")
                )
                server = EditServer()
                first, picture_set = await self.start(gateway, server)
                second = picture_set.candidates[1]
                history = [("user", "un héron"), ("assistant", first.reponse)]
                gateway.reply = rewrite_reply(
                    prompt=HERON.replace("pale sky", "orange sky"),
                    edit="Make the sky orange.",
                )
                done, item, _ = await self.follow(gateway, history, text)
                self.assertTrue(done)
                # The reading call is told which picture was named.
                sent = json.loads(gateway.follows[-1]["messages"][1]["content"])
                self.assertEqual(sent["named_picture"], 2)
                # One edit, of the second picture's own original.
                self.assertEqual(len(server.edits), 1)
                prompt, _, original = server.edits[0]
                self.assertTrue(prompt.startswith("Make the sky orange."))
                self.assertIn(cpu_turn.EDIT_KEEP, prompt)
                self.assertEqual(
                    base64.b64decode(original), store.image_bytes(second.render_key)
                )
                # No new set drawn: the two first pictures only.
                self.assertEqual(len(server.renders), 2)
                self.assertIn("Voici l'image modifiée", item.reponse)
                after = store.load(picture_set.set_id)
                assert after is not None
                self.assertEqual(after.artifact_candidate, 1)
                self.assertTrue(after.edited)
                # Version 2 holds the choice, version 3 the edit.
                self.assertEqual(after.artifact_version, 3)

    async def test_the_last_one_names_the_last_candidate(self) -> None:
        gateway = DialogueGateway(follow_reply("change", "Remove the boat."))
        server = EditServer()
        first, picture_set = await self.start(gateway, server)
        history = [("user", "un héron"), ("assistant", first.reponse)]
        gateway.reply = rewrite_reply(
            prompt=HERON + " No boat.", edit="Remove the boat."
        )
        done, _, _ = await self.follow(
            gateway, history, "la dernière, mais sans le bateau"
        )
        self.assertTrue(done)
        last = picture_set.candidates[-1]
        self.assertEqual(
            base64.b64decode(server.edits[0][2]), store.image_bytes(last.render_key)
        )

    async def test_a_named_picture_is_never_read_as_a_new_one(self) -> None:
        gateway = DialogueGateway(follow_reply("new"))
        server = EditServer()
        first, _ = await self.start(gateway, server)
        history = [("user", "un héron"), ("assistant", first.reponse)]
        gateway.reply = rewrite_reply(
            prompt=HERON + " It wears a red scarf.", edit="Add a red scarf."
        )
        done, item, _ = await self.follow(
            gateway, history, "keep the second image but add a red scarf"
        )
        self.assertTrue(done)
        self.assertEqual(len(server.edits), 1)
        self.assertEqual(len(server.renders), 2)
        # The person's own words reach the rewrite as the change.
        change = gateway.payloads[-1]["messages"][1]["content"]
        self.assertIn("add a red scarf", change)
        self.assertIn("/versions/3/open", item.reponse)

    async def test_a_number_beyond_the_pictures_shown_changes_none(self) -> None:
        gateway = DialogueGateway(follow_reply("change", "Make the sky orange."))
        server = EditServer()
        first, picture_set = await self.start(gateway, server)
        history = [("user", "un héron"), ("assistant", first.reponse)]
        done, item, _ = await self.follow(
            gateway, history, "dans la quatrième image, ciel orange"
        )
        self.assertTrue(done)
        self.assertIn("il n'y a pas d'image 4", item.reponse)
        self.assertEqual((server.edits, len(server.renders)), ([], 2))
        self.assertEqual(gateway.follows, [])
        after = store.load(picture_set.set_id)
        assert after is not None
        self.assertEqual(after.artifact_version, 1)

    async def test_separate_pictures_are_named_in_the_order_shown(self) -> None:
        gateway = DialogueGateway(follow_reply("change", "Add a moon."))
        server = EditServer()
        _, heron = await self.start(gateway, server)
        gateway.reply = rewrite_reply(prompt="A small yellow tram on a hill street.")
        tram_item = await self.run_turn(gateway, server, "un tramway jaune")
        tram = self.picture_set(tram_item)
        # Two answers that each showed one picture (their artifact links).
        links = [
            choice._artifact_link(heron, OWNER, "en"),
            choice._artifact_link(tram, OWNER, "en"),
        ]
        history: list[tuple[str, str]] = []
        for request_text, link in zip(("a heron", "a tram"), links):
            assert link is not None
            history += [
                ("user", request_text),
                ("assistant", "Here it is.\n\n" + link[0]),
            ]
        gateway.reply = rewrite_reply(prompt=HERON + " A moon.", edit="Add a moon.")
        done, item, _ = await self.follow(
            gateway, history, "on the first picture, add a moon"
        )
        self.assertTrue(done)
        self.assertEqual(
            base64.b64decode(server.edits[-1][2]),
            store.image_bytes(store.current_render(heron)[0]),
        )
        self.assertEqual(item.artifacts[0]["id"], heron.artifact_id)
        done, item, _ = await self.follow(gateway, history, "la troisième image")
        self.assertIn("Cette conversation montre 2 images", item.reponse)

    async def test_a_server_that_cannot_edit_keeps_the_picture_and_proposes(
        self,
    ) -> None:
        gateway = DialogueGateway(follow_reply("change", "Make the sky orange."))
        server = FakeServer()  # its health does not say it edits
        first, picture_set = await self.start(gateway, server)
        second = picture_set.candidates[1]
        history = [("user", "un héron"), ("assistant", first.reponse)]
        gateway.reply = rewrite_reply(
            prompt=HERON.replace("pale sky", "orange sky"), edit="Make the sky orange."
        )
        text = "keep the second image but make the sky orange"
        done, item, _ = await self.follow(gateway, history, text)
        self.assertTrue(done)
        self.assertEqual(len(server.renders), 2)  # nothing drawn from scratch
        self.assertEqual(item.images, [])
        self.assertIn("picture_cpu_no_edit_server", item.erreurs)
        self.assertIn("ne sait pas retoucher une image existante", item.reponse)
        self.assertIn("la version 2 reste telle quelle", item.reponse)
        after = store.load(picture_set.set_id)
        assert after is not None
        self.assertEqual(after.artifact_candidate, 1)  # the named one, kept
        self.assertTrue(after.proposed_redraw)
        # Accepted: the changed description drawn with the named picture's seed.
        gateway.follow = follow_reply("change", "")
        history += [("user", text), ("assistant", item.reponse)]
        done, again, _ = await self.follow(gateway, history, "yes")
        self.assertTrue(done)
        self.assertEqual(server.renders[-1], (server.renders[-1][0], second.seed))
        self.assertIn("orange sky", server.renders[-1][0])
        self.assertIn("n'a pas été retouchée sur place", again.reponse)

    async def test_an_edit_missed_by_the_check_is_edited_again_never_redrawn(
        self,
    ) -> None:
        gateway = CheckingDialogueGateway(
            follow_reply("change", "The heron holds exactly two fish.")
        )
        server = EditServer()
        first, picture_set = await self.start(gateway, server)
        second = picture_set.candidates[1]
        history = [("user", "un héron"), ("assistant", first.reponse)]
        gateway.reply = rewrite_reply(
            prompt=HERON + " It holds exactly two fish.",
            edit="Give the heron exactly two fish in its beak.",
            checks=["le héron tient deux poissons"],
        )
        gateway.met = ['{"met": [false], "seen": ["un poisson"]}', '{"met": [true]}']
        code = AsyncMock(return_value=None)
        with (
            patch.object(picture_turn, "available", lambda: True),
            patch.object(picture_turn, "_code_instead", code),
        ):
            done, item, _ = await self.follow(
                gateway, history, "sur la deuxième, deux poissons dans le bec"
            )
        self.assertTrue(done)
        self.assertEqual(len(server.edits), 2)
        self.assertEqual(len(server.renders), 2)  # no picture drawn from scratch
        code.assert_not_called()
        original = store.image_bytes(second.render_key)
        for prompt, _, sent in server.edits:
            self.assertEqual(base64.b64decode(sent), original)
        # The retry carries what the check found missing.
        self.assertIn("le héron tient deux poissons", server.edits[1][0])
        after = store.load(picture_set.set_id)
        assert after is not None
        self.assertTrue(after.edited)
        self.assertEqual(after.unmet, [])


class CodeDrawingReferenceTests(Base):
    """A set drawn by code: the named candidate's own source is edited."""

    def test_the_named_drawing_source_is_what_the_painter_edits(self) -> None:
        from packages.artifact_source import Block, prepare
        from services.orchestrator import artifact_store

        drawings = [
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
            f'<rect id="kite{n}" width="{n + 2}" height="3" fill="#c33"/></svg>'
            for n in range(1, 4)
        ]
        picture_set = store.create(
            OWNER,
            "fr",
            "Le cerf-volant",
            [
                store.Candidate(image_key=f"{n:032x}", svg=svg, source=svg)
                for n, svg in enumerate(drawings, start=1)
            ],
        )
        prepared = prepare(Block("svg", drawings[0], True), "Le cerf-volant", "fr")
        stored = artifact_store.create(
            OWNER,
            "Le cerf-volant",
            "le-cerf-volant.svg",
            prepared.document.encode("utf-8"),
            "request-1",
        )
        picture_set.artifact_id = str(stored["id"])
        picture_set.artifact_version = int(stored["version"])
        picture_set.request = "un cerf-volant rouge"
        picture_set.current_source = drawings[0]
        store.update(picture_set)
        store.link_artifact(picture_set)
        answer = "\n".join(
            f"/images/picture/{picture_set.set_id}/{n}" for n in (1, 2, 3)
        )
        link = choice._artifact_link(picture_set, OWNER, "fr")
        assert link is not None
        request = Request(
            "sur le troisième dessin, enlève seulement la queue",
            [("user", "un cerf-volant"), ("assistant", answer + "\n\n" + link[0])],
        )
        current, target = reference.current_for(request, None, "request-2")
        assert target is not None
        self.assertEqual((target.number, target.index), (3, 2))
        self.assertEqual(current.artifact["artifact_id"], picture_set.artifact_id)
        after = store.load(picture_set.set_id)
        assert after is not None
        self.assertEqual(after.current_source, drawings[2])
        first = [
            {"role": "system", "content": "painter"},
            {"role": "user", "content": "un cerf-volant rouge"},
        ]
        with patch.object(painter, "messages", lambda *a, **k: list(first)):
            messages, redrawn = followup.messages(after, "Remove the tail.")
        self.assertFalse(redrawn)
        self.assertEqual(messages[-2], {"role": "assistant", "content": drawings[2]})
        self.assertIn("Remove the tail.", messages[-1]["content"])
        self.assertIn(
            "Keep everything the change does not touch", messages[-1]["content"]
        )


if __name__ == "__main__":
    unittest.main()
