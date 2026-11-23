"""What Atlas says about its own app file rests on measured facts.

Staging -178: after "it does not work, nothing shows", the answer said the
flags were embedded "only inside Atlas" and could not load in an ordinary
browser; the downloaded file carried them as data: URIs and loaded nothing
from the network. Fictional content throughout.
"""

from __future__ import annotations

import os
import struct
import tempfile
import unittest
import zlib
from unittest.mock import AsyncMock, patch

from packages.artifact_media import Media
from services.orchestrator import app_facts
from services.orchestrator.app_render import RenderReport
from services.orchestrator.app_turn import process_app_turn
from services.orchestrator.artifact_tools import current_artifact_message
from services.orchestrator.chat_schema import ChatRequest
from services.orchestrator.interactions import Interaction
from services.orchestrator.stream_client import sink_context

OWNER = "quizmaker@example.test"
FLAG = "https://pictures.example.test/flags/Flag_of_Sylvania.png"


def _png() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(b"\x00\xff\x80\x00"))
        + chunk(b"IEND", b"")
    )


QUIZ = (
    '<!DOCTYPE html>\n<html lang="fr">\n<head><title>Capitales de Sylvanie</title>'
    "</head>\n<body>\n<h1>Capitales de Sylvanie</h1>\n"
    '<img id="flag" src="atlas-media:flag" alt="Drapeau">\n'
    '<button id="go">Commencer</button>\n<script>\n'
    "document.getElementById('go').onclick = () => { document.body.dataset.on = '1'; };\n"
    "</script>\n</body>\n</html>"
)
FALSE_LEAD = (
    "J'ai revérifié l'appli : dans l'aperçu, tout s'affiche, sans erreur.\n\n"
    "Une piste réelle : les drapeaux sont intégrés uniquement à l’intérieur "
    "d’Atlas. Si tu as ouvert le fichier téléchargé « quiz.html » dans un "
    "navigateur classique, les images ne peuvent pas s’y charger. Réessaie "
    "depuis le panneau Atlas.\n\n"
    "Dis-moi ce que tu vois exactement ?"
)


class Measures(unittest.TestCase):
    def test_inline_files_and_network_loads(self) -> None:
        page = (
            '<img src="data:image/png;base64,AAAA"><a href="https://site.example.test/">'
            "site</a><style>body{background:url(data:image/png;base64,BBBB)}</style>"
        )
        facts = app_facts.measure(page, ["Flag_of_Sylvania.png"])
        self.assertEqual((facts.inline, facts.external), (2, []))
        self.assertTrue(facts.self_contained)
        loading = app_facts.measure(
            '<script src="https://cdn.example.test/lib.js"></script>'
            "<script>fetch('https://api.example.test/q')</script>"
            '<link rel="stylesheet" href="//fonts.example.test/f.css">'
        )
        self.assertEqual(
            loading.external,
            [
                "https://cdn.example.test/lib.js",
                "//fonts.example.test/f.css",
                "https://api.example.test/q",
            ],
        )
        self.assertEqual(loading.scripts, ["https://cdn.example.test/lib.js"])
        note = app_facts.model_note(loading, 2)
        self.assertIn("https://cdn.example.test/lib.js", note)
        self.assertIn("need an internet connection", note)

    def test_the_note_states_a_self_contained_file(self) -> None:
        facts = app_facts.measure(
            '<img src="data:image/png;base64,AAAA">', ["Flag_of_Sylvania.png"]
        )
        note = app_facts.model_note(facts, 3)
        self.assertIn("files embedded inline as data: URIs: 1", note)
        self.assertIn("Flag_of_Sylvania.png", note)
        self.assertIn("loads nothing from the network", note)
        self.assertIn("browser cache", note)


class Claims(unittest.TestCase):
    contained = app_facts.FileFacts(30_000, 6, ["Flag_of_Sylvania.png"])
    loading = app_facts.FileFacts(
        30_000,
        0,
        [],
        ["https://cdn.example.test/lib.js"],
        ["https://cdn.example.test/lib.js"],
    )

    def test_a_false_lead_is_removed_and_the_fact_said(self) -> None:
        text, kinds = app_facts.checked_answer(FALSE_LEAD, self.contained, "fr")
        self.assertEqual(kinds, ["place", "place"])
        self.assertNotIn("uniquement", text)
        self.assertNotIn("ne peuvent pas", text)
        self.assertIn("tout s'affiche, sans erreur.", text)
        self.assertIn("Dis-moi ce que tu vois exactement ?", text)
        self.assertTrue(text.endswith(app_facts.CORRECTION["fr"]))

    def test_english_and_network_claims(self) -> None:
        text, kinds = app_facts.checked_answer(
            "The quiz is ready. The pictures only work inside Atlas. "
            "Press Start to begin.\n\nIt needs an internet connection to show the flags.",
            self.contained,
            "en",
        )
        self.assertEqual(kinds, ["place", "network"])
        self.assertIn("The quiz is ready. Press Start to begin.", text)
        self.assertNotIn("only work inside Atlas", text)
        self.assertNotIn("internet connection to show", text)
        self.assertIn(app_facts.CORRECTION["en"], text)

    def test_true_statements_are_kept(self) -> None:
        for text in (
            "Ne t'inquiète pas : l'appli fonctionne aussi en dehors d'Atlas.",
            "Le score s'affiche en haut. Appuie sur Commencer.",
            "Les drapeaux sont intégrés au fichier.",
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    app_facts.checked_answer(text, self.contained, "fr"), (text, [])
                )
        # A file that does load from the network does need it.
        claim = "Elle a besoin d'une connexion à internet pour la police."
        self.assertEqual(
            app_facts.checked_answer(claim, self.loading, "fr"), (claim, [])
        )


class BrokenTurn(unittest.IsolatedAsyncioTestCase):
    """The "nothing shows" follow-up of a quiz with an embedded flag."""

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        environment = patch.dict(
            os.environ,
            {
                "ATLAS_DATA_DIR": self.directory.name,
                "ATLAS_ARTIFACTS": "1",
                "ATLAS_EXPECTATIONS": "0",
            },
        )
        environment.start()
        self.addCleanup(environment.stop)
        self.flag = Media(
            "flag", "image/png", _png(), FLAG, "Flag_of_Sylvania.png", "CC0", "person"
        )

    async def turn(
        self, text: str, outcomes: list[object], history: list[dict[str, str]]
    ) -> tuple[Interaction, object]:
        from test_app_turn import FakeModel

        request = ChatRequest.model_validate(
            {"messages": [*history, {"role": "user", "content": text}], "lang": "fr"}
        )
        request.__dict__["artifact_owner"] = OWNER
        current = current_artifact_message(
            [{"role": m.role, "content": m.text} for m in request.messages], OWNER
        )
        model = FakeModel(outcomes)
        item = Interaction()

        async def sink(event: dict[str, object]) -> None:
            return None

        token = sink_context.set(sink)
        try:
            with (
                patch(
                    "services.orchestrator.app_turn.GatewayModel.connect",
                    AsyncMock(return_value=model),
                ),
                patch(
                    "services.orchestrator.app_turn.app_media.fetch_media",
                    AsyncMock(return_value=([self.flag], [])),
                ),
                patch(
                    "services.orchestrator.app_turn.app_render.check",
                    AsyncMock(return_value=RenderReport("ok")),
                ),
            ):
                await process_app_turn(
                    request,
                    item,
                    intent="modify" if history else "create",
                    kind="app",
                    current=current,
                )
        finally:
            sink_context.reset(token)
        return item, model

    async def test_the_answer_rests_on_the_stored_file(self) -> None:
        first = f"fais un quiz des capitales avec ce drapeau, libre de droits : {FLAG}"
        built, _ = await self.turn(
            first, [f"```html\n{QUIZ}\n```\nLe quiz est prêt."], []
        )
        self.assertEqual(built.artifacts[0]["version"], 1)
        history = [
            {"role": "user", "content": first},
            {"role": "assistant", "content": built.reponse},
        ]
        item, model = await self.turn(
            "ça marche pas, rien ne s'affiche", [FALSE_LEAD], history
        )
        # The writer read the measured facts of version 1 before answering.
        system = "\n".join(
            str(message["content"])
            for message in model.calls[0]  # type: ignore[attr-defined]
            if message["role"] == "system"
        )
        self.assertIn("Measured facts about version 1", system)
        self.assertIn("files embedded inline as data: URIs: 1", system)
        self.assertIn("loads nothing from the network", system)
        # Its false lead is gone, the fact is said, the question stays.
        self.assertNotIn("uniquement", item.reponse)
        self.assertNotIn("ne peuvent pas", item.reponse)
        self.assertIn(app_facts.CORRECTION["fr"], item.reponse)
        self.assertIn("Dis-moi ce que tu vois exactement ?", item.reponse)
        self.assertEqual(item.artifacts, [])


class FallbackClaims(unittest.TestCase):
    """Staging -181: "I also added a visible message if JavaScript is off"
    on a version without any <noscript>."""

    bare = app_facts.measure('<div id="question"></div><script>go();</script>')

    def test_a_claimed_no_script_message_is_removed(self) -> None:
        text = (
            "J'ai changé une seule chose dans le fichier. J'ai aussi ajouté un "
            "message visible si JavaScript est désactivé ou bloqué par le "
            "navigateur. Si tu vois toujours une page vide, dis-moi ce que tu vois."
        )
        checked, kinds = app_facts.checked_answer(text, self.bare, "fr")
        self.assertEqual(kinds, ["no_script"])
        self.assertEqual(
            checked,
            "J'ai changé une seule chose dans le fichier. Si tu vois toujours une "
            "page vide, dis-moi ce que tu vois.",
        )
        with_message = app_facts.measure(
            "<noscript>Activez JavaScript pour jouer.</noscript><script>go();</script>"
        )
        self.assertEqual(app_facts.checked_answer(text, with_message, "fr"), (text, []))

    def test_picture_and_script_failure_claims(self) -> None:
        text = (
            "If a flag fails to load, a placeholder with the initial shows instead. "
            "Even if the script fails, a message explains what happened. "
            "Press Start."
        )
        checked, kinds = app_facts.checked_answer(text, self.bare, "en")
        self.assertEqual(kinds, ["image_error", "script_error"])
        self.assertEqual(checked, "Press Start.")
        handled = app_facts.measure(
            "<img onerror=\"this.alt='A'\"><script>try { go(); } catch (e) {}</script>"
        )
        self.assertEqual(handled.states, frozenset({"image_error", "script_error"}))
        self.assertEqual(app_facts.checked_answer(text, handled, "en"), (text, []))

    def test_advice_about_javascript_is_kept(self) -> None:
        advice = "Vérifie que JavaScript n'est pas désactivé dans ton navigateur."
        self.assertEqual(
            app_facts.checked_answer(advice, self.bare, "fr"), (advice, [])
        )

    def test_the_note_lists_the_fallback_states(self) -> None:
        note = app_facts.model_note(self.bare, 4)
        self.assertIn("fallback states it has: none", note)
        self.assertIn("<noscript>", note)
