"""A Work run the model service stops: run once more while the turn has time,
else said with its code and status (a usage limit as a limit), never handed
to a chat that would deny making files. Requests are fictional."""

from __future__ import annotations

import contextlib
import os
from typing import Any, Iterator
import unittest
from unittest.mock import AsyncMock, patch

from services.orchestrator import code_mode
from services.orchestrator.chat_schema import ChatRequest
from services.orchestrator.chat_work import (
    WORK_STOPPED,
    escalate_to_work,
    work_stopped_instruction,
)
from services.orchestrator.interactions import Interaction
from services.orchestrator.model import GatewayError
from services.orchestrator.request_router import Decision


def request(text: str, locale: str = "en") -> ChatRequest:
    value = ChatRequest.model_validate(
        {"messages": [{"role": "user", "content": text}], "ui_locale": locale}
    ).model_copy(update={"lang": locale})
    value.__dict__["native_chat_id"] = "123e4567-e89b-12d3-a456-426614174010"
    value.__dict__["artifact_owner"] = "user@example.com"
    return value


@contextlib.contextmanager
def work(runs: Any, left: float | None = 200.0) -> Iterator[None]:
    with (
        patch.dict(os.environ, {"ATLAS_CHAT_WORK": "1"}),
        patch("services.orchestrator.chat_work.work_gate_open", return_value=True),
        patch("services.orchestrator.chat_work.work_time_limit", return_value=left),
        patch(
            "services.orchestrator.chat_work._identity",
            new=AsyncMock(
                return_value={
                    "memberships": [{"org_id": "123e4567-e89b-12d3-a456-426614174000"}]
                }
            ),
        ),
        patch(
            "services.orchestrator.chat_work._ensure_scratch_project",
            new=AsyncMock(return_value="123e4567-e89b-12d3-a456-426614174002"),
        ),
        patch(
            "services.orchestrator.chat_work._find_binding",
            new=AsyncMock(
                return_value={
                    "conversation_id": "123e4567-e89b-12d3-a456-426614174003",
                    "work_session_id": "123e4567-e89b-12d3-a456-426614174004",
                }
            ),
        ),
        patch("services.orchestrator.code_mode.process_work", new=runs),
        # The model router decided a file (the lexical gate reads fewer words).
        patch(
            "services.orchestrator.request_router.decision",
            return_value=Decision.model_validate({"kind": "file"}),
        ),
    ):
        yield


def stopped(item: Interaction) -> None:
    """What process_work leaves when the loop stops on the model service."""
    item.state, item.reponse, item.cout_eur = "failed", "Work failed: x", 0.02
    item.erreurs.append("provider_error")
    item.work_failure = dict(
        category=code_mode.work_failure_category("provider_error", tool_failure=False)
        or "",
        detail=code_mode.provider_detail("provider_error"),
        answer="",
    )


class ProviderStopTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_stopped_run_is_run_once_more_while_time_allows(self) -> None:
        calls: list[object] = []

        async def runs(r: Any, i: Interaction, **kwargs: Any) -> None:
            calls.append(kwargs.get("turn_request_id"))
            if len(calls) == 1:
                stopped(i)
            else:
                i.state, i.reponse, i.cout_eur = "done", "Here is the file.", 0.03

        item = Interaction()
        with work(runs):
            self.assertTrue(
                await escalate_to_work(
                    request("Make me a Word file of the bakery's opening hours"), item
                )
            )
        self.assertEqual(len(calls), 2)
        self.assertIsNotNone(calls[1])
        self.assertEqual((item.state, item.reponse), ("done", "Here is the file."))
        self.assertAlmostEqual(item.cout_eur, 0.05)
        self.assertNotIn("provider_error", item.erreurs)

    async def test_stopped_twice_is_said_never_answered_by_the_chat(self) -> None:
        async def runs(r: Any, i: Interaction, **kwargs: Any) -> None:
            stopped(i)

        item = Interaction()
        req = request("Mach mir bitte eine Excel-Tabelle für den Vereinskalender", "de")
        with work(runs):
            self.assertTrue(await escalate_to_work(req, item))
        self.assertEqual(item.state, "done")
        self.assertIn("KI-Modelldienst", item.reponse)
        self.assertIn("(provider_error)", item.reponse)
        self.assertNotIn("{detail}", item.reponse)
        self.assertFalse(req.__dict__.get("work_stopped"))

    async def test_without_time_for_another_run_the_stop_is_said(self) -> None:
        calls: list[object] = []

        async def runs(r: Any, i: Interaction, **kwargs: Any) -> None:
            calls.append(1)
            stopped(i)

        item = Interaction()
        with work(runs, left=40.0):
            self.assertTrue(
                await escalate_to_work(
                    request("Fais-moi un PDF du programme de la chorale", "fr"), item
                )
            )
        self.assertEqual(len(calls), 1)
        self.assertIn("service de modèles d'IA", item.reponse)

    async def test_a_usage_limit_is_a_limit_and_is_not_retried(self) -> None:
        calls: list[object] = []

        async def runs(r: Any, i: Interaction, **kwargs: Any) -> None:
            calls.append(1)
            raise GatewayError("provider_quota", 429)

        item = Interaction()
        with work(runs):
            self.assertTrue(
                await escalate_to_work(
                    request("Make me a PowerPoint about the ferry timetable"), item
                )
            )
        self.assertEqual(len(calls), 1)
        self.assertIn("usage limit", item.reponse)
        self.assertIn("(provider_quota, HTTP 429)", item.reponse)
        self.assertIn("not an outage", item.reponse)

    async def test_a_service_error_raised_twice_is_said_with_its_status(self) -> None:
        calls: list[object] = []

        async def runs(r: Any, i: Interaction, **kwargs: Any) -> None:
            calls.append(1)
            raise GatewayError("provider_error", 502)

        item = Interaction()
        with work(runs):
            self.assertTrue(
                await escalate_to_work(
                    request("Hazme un documento Word con el menú del comedor", "es"),
                    item,
                )
            )
        self.assertEqual(len(calls), 2)
        self.assertIn("(provider_error, HTTP 502)", item.reponse)
        self.assertIn("servicio de modelos de IA", item.reponse)


class ChatAfterStopTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_chat_answering_alone_is_told_the_run_stopped(self) -> None:
        async def runs(r: Any, i: Interaction, **kwargs: Any) -> None:
            i.state, i.reponse = "failed", "partial"
            i.erreurs.append("deliverable_group_required")

        req = request("Make me a Word file of the allotment rota")
        with work(runs):
            self.assertFalse(await escalate_to_work(req, Interaction()))
        text = work_stopped_instruction(req)
        self.assertEqual(text, WORK_STOPPED)
        self.assertIn("Never say that you have no tools", text)
        self.assertEqual(work_stopped_instruction(request("hello")), "")


class WordingTests(unittest.TestCase):
    def test_every_language_names_the_service_stop_and_the_limit(self) -> None:
        for language, words in code_mode.WORK_FAILURE.items():
            for kind in ("provider", "limit"):
                with self.subTest(language=language, kind=kind):
                    self.assertIn("{detail}", words[kind])
                    reply = code_mode.provider_failure_reply(
                        language, "rate_limit" if kind == "limit" else "x", 503
                    )
                    self.assertIn("HTTP 503", reply)
                    self.assertNotIn("{", reply)

    def test_a_stopped_reason_never_shows_a_placeholder(self) -> None:
        item = Interaction()
        item.state = "failed"
        item.erreurs.append("provider_error")
        for language in code_mode.WORK_FAILURE:
            with self.subTest(language=language):
                reply = code_mode.stopped_work_reply(item, language)
                self.assertIn("(provider_error)", reply)
                self.assertNotIn("{", reply)


if __name__ == "__main__":
    unittest.main()
