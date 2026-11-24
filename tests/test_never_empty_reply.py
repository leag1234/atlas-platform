"""A reply is never empty, and a request about the conversation's own delivery
is answered from the conversation (class suite, staging 2026-11-24).

Two defects of one class run, by class:

- a turn whose required web search failed before any page was read ended
  with an error event and no text: the person read an empty reply;
- a message about what the conversation had delivered (send its link again,
  restate its status under it) read "link" or "version" as a request for web
  pages, so a mandatory search ran at all.

Fictional conversations in five languages; no model, no search is called.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, Mock, patch

from services.orchestrator.chat_pipeline import process, search_failure_notice
from services.orchestrator.chat_schema import ChatRequest
from services.orchestrator.chat_stream import response
from services.orchestrator.interactions import Interaction
from services.orchestrator.link_requests import (
    SEARCH_FAILED,
    SEARCH_FAILURES,
    failed_links_reply,
    failed_search_answer,
    own_delivery,
    page_request,
)
from services.orchestrator.loop import Query, Reservation, Turn, run
from services.orchestrator.model import Configuration, GatewayError, GatewayModel
from services.orchestrator.searxng import NOTICES, OUTCOMES
from services.orchestrator.search_policy import required_research

APP = "/api/atlas/artifacts/" + "3f" * 16 + "/versions/2/download"
SAVED = (
    "/api/atlas/orgs/0b7c58a2-4d1e-4f7a-9a51-2c6f0e9d3b11/projects/"
    "5e2a9c10-77b3-4c8e-a0f4-91d2b6c3e7a8/deliverables/"
    "c41d8e2f-0a6b-4e95-8d3c-7f1a2b9e6d54/download"
)
WORKSPACE = "/api/v1/terminals/atlas-files/files/serve/notes/plan.pdf"


def delivered(link: str, name: str) -> tuple[str, ...]:
    return (
        "Build me the thing, please.",
        f"Here it is.\n\n[{name} herunterladen]({link})",
    )


# (language, delivered link, file name, the person's message about it)
ABOUT_THE_DELIVERY = (
    (
        "de",
        APP,
        "zaehler.html",
        "Schick mir bitte nochmal den Link zur selben HTML-Datei und schreib "
        "darunter „Prüfung: offen“. Die Datei nicht ändern.",
    ),
    (
        "de",
        APP,
        "zaehler.html",
        "Da kam kein Link. Den Downloadlink zur Version 2 bitte, sonst nichts.",
    ),
    (
        "fr",
        SAVED,
        "budget-atelier.pdf",
        "Tu peux me renvoyer le lien du même fichier PDF ? Ne le modifie pas.",
    ),
    (
        "es",
        WORKSPACE,
        "plan.pdf",
        "¿Me pasas otra vez el enlace al mismo archivo? Y pon debajo el estado "
        "actual de la revisión.",
    ),
    (
        "it",
        SAVED,
        "orario-corsi.docx",
        "Rimandami il link dello stesso documento, senza cambiarlo.",
    ),
    (
        "en",
        APP,
        "quiz.html",
        "resend the link to that app and say under it whether you tested the "
        "latest version in a browser",
    ),
)


class OwnDeliveryTests(unittest.TestCase):
    def test_a_message_about_the_delivery_needs_no_research(self) -> None:
        for language, link, name, message in ABOUT_THE_DELIVERY:
            with self.subTest(language=language, message=message):
                history = delivered(link, name)
                self.assertTrue(own_delivery(message, history))
                self.assertFalse(page_request(message, history))
                self.assertEqual(
                    required_research(message, language, history=history), ()
                )

    def test_without_a_delivered_link_the_same_words_keep_their_route(self) -> None:
        history = ("Build me the thing, please.", "Here is a summary, no file.")
        message = "Send me the link to that app's official site"
        self.assertFalse(own_delivery(message, history))
        self.assertTrue(page_request(message, history))

    def test_links_to_outside_pages_keep_their_research(self) -> None:
        # A delivery in the conversation does not turn every link request
        # into one about it: the pages asked for are not the delivery.
        for language, message in (
            ("en", "Give me links to three shops selling cedar planters"),
            ("fr", "Donne-moi des liens vers des boutiques de lampes en laiton"),
            ("de", "Gib mir Links zu Läden für Wanderstöcke aus Esche"),
        ):
            with self.subTest(message=message):
                history = delivered(APP, "quiz.html")
                self.assertFalse(own_delivery(message, history))
                self.assertTrue(page_request(message, history))
                calls = required_research(message, language, history=history)
                self.assertTrue(calls)
                self.assertEqual(calls[0].name, "web_search")


class FailedSearchReplyTests(unittest.TestCase):
    def test_every_search_outcome_is_a_search_failure(self) -> None:
        self.assertLessEqual(OUTCOMES, SEARCH_FAILURES)

    def test_every_failure_gives_a_reply_in_the_person_language(self) -> None:
        for code in sorted(SEARCH_FAILURES):
            for language in ("en", "fr", "de", "es", "it"):
                with self.subTest(code=code, language=language):
                    reply = failed_search_answer(code, "", language)
                    self.assertTrue(reply.startswith(SEARCH_FAILED[language]))
                    self.assertNotIn(code, reply)

    def test_a_quota_is_named_as_a_quota_with_its_status(self) -> None:
        detail = "Web search unavailable: quota exhausted. Provider HTTP 432."
        reply = failed_search_answer("quota_exceeded", detail, "de")
        self.assertIn("Kontingent", reply)
        self.assertIn("HTTP 432", reply)
        self.assertNotIn("nicht verfügbar", reply)
        reply = failed_links_reply("quota_exceeded", detail, "kayak prices", (), "en")
        self.assertIn("HTTP 432", reply)
        self.assertIn("verified links", reply)

    def test_a_blocked_search_says_it_was_blocked(self) -> None:
        for language in ("en", "fr", "de", "es", "it"):
            with self.subTest(language=language):
                self.assertIn(
                    search_failure_notice("upstream_blocked", language),
                    failed_search_answer("upstream_blocked", "", language),
                )


class _Run:
    """A chat turn through the whole pipeline, model and search recorded."""

    def __init__(self, answer: str, search_result: dict[str, object]) -> None:
        self.model = GatewayModel(
            "http://unused.invalid",
            Configuration(
                input_eur_per_mtok=Decimal(0),
                output_eur_per_mtok=Decimal(0),
                max_tokens=1000,
            ),
        )
        self.complete = AsyncMock(
            return_value=Turn(answer, usage=Reservation(0, Decimal(0)))
        )
        self.search = AsyncMock(return_value=search_result)

    async def turn(self, messages: list[dict[str, str]], locale: str) -> Interaction:
        item = Interaction()
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(
                "os.environ",
                {
                    "ATLAS_WEB_CACHE": directory + "/cache",
                    "ATLAS_TERMINAL_ENABLED": "0",
                    "ATLAS_SEARCH_PROVIDER": "searxng",
                },
            ),
            patch(
                "services.orchestrator.chat_pipeline.GatewayModel.connect",
                new=AsyncMock(return_value=self.model),
            ),
            patch.object(self.model, "complete", new=self.complete),
            patch("services.orchestrator.tools.Runtime.search", new=self.search),
        ):
            await process(
                ChatRequest.model_validate({"messages": messages, "ui_locale": locale}),
                item,
            )
        return item


BLOCKED: dict[str, object] = {
    "provider": "searxng",
    "outcome": "upstream_blocked",
    "results": [],
}


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_failed_required_search_is_never_answered_from_memory(
        self,
    ) -> None:
        for language, question in (
            ("fr", "Quel est le prix actuel du vélo cargo Mistralo 7 ?"),
            ("de", "Was kostet die Stirnlampe Talgrund X3 heute?"),
            ("es", "¿Cuál es la última versión del editor Pluma Lenta?"),
            ("it", "Qual è il prezzo attuale della tenda Vallorsa 2?"),
            ("en", "What is the latest release of the Quillwright note app?"),
        ):
            with self.subTest(language=language):
                run_ = _Run("never written", BLOCKED)
                # An API client reads the refusal and its status (D5); the
                # chat says it as the answer (StreamTests).
                with self.assertRaises(GatewayError) as raised:
                    await run_.turn([{"role": "user", "content": question}], language)
                self.assertEqual(raised.exception.code, "upstream_blocked")
                run_.complete.assert_not_awaited()

    async def test_a_link_request_whose_search_was_blocked_gets_its_terms(
        self,
    ) -> None:
        for language, question, label in (
            (
                "fr",
                "Donne-moi des liens vers des boutiques qui vendent la lampe Brasilune",
                "liens vérifiés",
            ),
            (
                "de",
                "Gib mir Links zu Shops für den Rucksack Felsgrat 30",
                "verifizierten Links",
            ),
            (
                "en",
                "Send me links to shops selling the Ottercliff tent",
                "verified links",
            ),
        ):
            with self.subTest(language=language):
                run_ = _Run("never written", BLOCKED)
                item = await run_.turn(
                    [{"role": "user", "content": question}], language
                )
                self.assertEqual(item.state, "done")
                self.assertIn(label, item.reponse)
                self.assertIn(
                    search_failure_notice("upstream_blocked", language), item.reponse
                )
                self.assertNotIn("never written", item.reponse)

    async def test_a_message_about_the_delivery_runs_no_search(self) -> None:
        for language, link, name, message in ABOUT_THE_DELIVERY:
            with self.subTest(language=language, message=message):
                answer = f"[{name}]({link})\nOK"
                run_ = _Run(answer, BLOCKED)
                earlier = delivered(link, name)
                item = await run_.turn(
                    [
                        {"role": "user", "content": earlier[0]},
                        {"role": "assistant", "content": earlier[1]},
                        {"role": "user", "content": message},
                    ],
                    language,
                )
                run_.search.assert_not_awaited()
                self.assertEqual(item.state, "done")
                self.assertTrue(item.reponse.strip())


ASKED = {
    "fr": "Quels sont les horaires actuels de la piscine des Tilleuls à Varenne ?",
    "de": "Welche Öffnungszeiten hat das Freibad Lindengrund heute?",
    "es": "¿Cuál es el horario actual de la biblioteca de Valdeolmos?",
    "it": "Qual è l'orario attuale del museo civico di Borgofiore?",
    "en": "What are the current opening hours of the Larkmoor lido?",
}


class StreamTests(unittest.IsolatedAsyncioTestCase):
    async def stream(
        self, code: str, detail: str, language: str, *, chat: bool
    ) -> tuple[str, list[dict[str, object]]]:
        async def failing(payload: ChatRequest, item: Interaction) -> None:
            raise GatewayError(code, 503, detail)

        payload = ChatRequest.model_validate(
            {
                "atlas_ui": chat,
                "stream": True,
                "ui_locale": language,
                "messages": [{"role": "user", "content": ASKED[language]}],
            }
        )
        item = Interaction()
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, {"ATLAS_INTERACTION_DIR": directory}),
        ):
            rows = [
                json.loads(str(row).removeprefix("data: "))
                async for row in response(
                    payload, item, time.monotonic(), failing
                ).body_iterator
                if str(row).startswith("data: {")
            ]
        text = "".join(
            str(row.get("choices", [{}])[0].get("delta", {}).get("content") or "")
            for row in rows
        )
        return text, [row for row in rows if "error" in row]

    async def test_the_chat_reads_a_reply_never_an_empty_one(self) -> None:
        for language in ("fr", "de", "es", "it", "en"):
            for code, detail in (
                ("upstream_blocked", NOTICES["upstream_blocked"]),
                ("upstream_timeout", NOTICES["upstream_timeout"]),
                (
                    "quota_exceeded",
                    "Web search unavailable: quota exhausted. Provider HTTP 432.",
                ),
            ):
                with self.subTest(language=language, code=code):
                    text, errors = await self.stream(code, detail, language, chat=True)
                    self.assertEqual(errors, [])
                    self.assertIn(SEARCH_FAILED[language], text)
                    self.assertNotIn(code, text)

    async def test_an_api_client_keeps_the_refusal_and_its_code(self) -> None:
        text, errors = await self.stream(
            "quota_exceeded", "Provider HTTP 432.", "en", chat=False
        )
        self.assertEqual(text.strip(), "")
        self.assertEqual(len(errors), 1)
        error = errors[0]["error"]
        assert isinstance(error, dict)
        self.assertEqual(error["code"], "quota_exceeded")


class LoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_delivery_link_in_the_answer_starts_no_link_search(self) -> None:
        # The answer gives back the delivered link, which no search retrieved:
        # for a request about outside pages the loop would search for links.
        link = SAVED
        answer = f"[Download budget-atelier.pdf]({link})\nVérification : non faite"
        model = Mock(
            estimate=Mock(return_value=Reservation(100, Decimal("0.001"))),
            complete=AsyncMock(return_value=Turn(answer)),
        )
        tools = Mock(
            estimate=Mock(return_value=Reservation(0, Decimal("0.001"))),
            execute=AsyncMock(return_value={"error": "unavailable"}),
        )
        result = await run(
            Query(question="Renvoie-moi le lien du même fichier PDF", lang="fr"),
            model,
            tools,
            "",
            history=[
                {"role": "user", "content": "Fais-moi le budget en PDF"},
                {"role": "assistant", "content": f"Voilà.\n\n[budget]({link})"},
            ],
        )
        self.assertEqual(result.state, "done")
        self.assertIn(link, result.text)
        tools.execute.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
