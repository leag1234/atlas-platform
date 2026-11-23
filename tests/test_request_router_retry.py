"""The routing call under a slow provider: hedged calls, then the local classifier.

Staging -93 (2026-11-16): the routing call on the fast profile got no first byte
within its 8 s bound; the lexical fallback then answered an English request for
an illustrated Word how-to in the chat, with no file. Staging 2026-11-23: 13 of
631 turns had no decision in 8 s. The replies below are the gateway's
/agent/complete shape as recorded on the staging gateway on 2026-11-16.
"""

import asyncio
import os
import time
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import patch

import yaml

from packages.work_intent import work_reason
from services.orchestrator import request_router
from services.orchestrator.chat_schema import ChatRequest
from services.orchestrator.request_router import COST_KEY, Decision

# Recorded on the staging gateway, 2026-11-16 (fast profile, reasoning off).
RECORDED_FILE = {
    "text": '{"kind": "file", "file_format": "docx", "follow_up": false, '
    '"needs_research": false, "needs_location": false, "origin": null}',
    "calls": [],
    "cost_eur": "0.0008876",
    "trace_tokens": 0,
    "reasoning_effort": "none",
}
RECORDED_ANSWER = {
    "text": '{"kind": "answer", "file_format": null, "follow_up": false, '
    '"needs_research": false, "needs_location": false, "origin": null}',
    "calls": [],
    "cost_eur": "0.0008784",
}

# Requests for an illustrated how-to or another file, in the five interface
# languages, with the format the fallback must keep.
DELIVERABLES = {
    "en": (
        "Can you put together a Word document that explains how to repot an "
        "orchid, with an illustration for each step?",
        "docx",
    ),
    "fr": (
        "tu peux me faire une présentation sur le cycle de l'eau avec une image "
        "par diapo stp",
        "pptx",
    ),
    "de": ("Erstelle bitte eine Excel-Tabelle für unser Haushaltsbudget", "xlsx"),
    "es": (
        "hazme un pdf con los pasos para cambiar una rueda, con un dibujo por paso",
        "pdf",
    ),
    "it": (
        "mi prepari un documento word con la ricetta della carbonara e le foto?",
        "docx",
    ),
}


def request(text: str) -> ChatRequest:
    return ChatRequest.model_validate(
        {"messages": [{"role": "user", "content": text}], "ui_locale": "en"}
    )


class Recorded:
    """A gateway that replies per text role (else per profile): a recorded reply,
    a delay, or an error."""

    def __init__(self, **plan: tuple[float, Any]) -> None:
        self.plan = plan
        self.asked: list[tuple[str, float, float]] = []
        self.payloads: list[dict[str, Any]] = []
        self.started = time.monotonic()

    async def __call__(self, payload: dict[str, Any], seconds: float) -> dict[str, Any]:
        name = payload.get("text_role") or payload["profile"]
        self.asked.append((name, time.monotonic() - self.started, seconds))
        self.payloads.append(payload)
        delay, outcome = self.plan[name]
        await asyncio.sleep(delay)
        if isinstance(outcome, Exception):
            raise outcome
        reply: dict[str, Any] = outcome
        return reply


@contextmanager
def router(
    bound: str, minimum: float | None = None, hedge_ms: str = "900"
) -> Iterator[None]:
    with patch.dict(
        os.environ,
        {
            "ATLAS_MODEL_ROUTER": "1",
            "ATLAS_MODEL_ROUTER_TIMEOUT": bound,
            "ATLAS_MODEL_ROUTER_HEDGE_MS": hedge_ms,
        },
    ):
        os.environ.pop("ATLAS_MODEL_ROUTER_PROFILE", None)
        if minimum is None:
            yield
        else:
            with patch.object(request_router, "MIN_ATTEMPT_SECONDS", minimum):
                yield


FIRST, SECOND = request_router.profiles()


class ProfileTests(unittest.TestCase):
    def test_roles_name_a_fast_first_profile_and_another_retry_profile(self) -> None:
        routing = yaml.safe_load(
            Path("services/model-gateway/routing.yaml").read_text()
        )
        roles = routing["picture_roles"]
        with patch.dict(os.environ, {"ATLAS_MODEL_ROUTER_PROFILE": ""}):
            self.assertEqual(request_router.profile(), roles["request_router"])
            self.assertEqual(
                request_router.profiles(),
                (roles["request_router"], roles["request_router_retry"]),
            )
        self.assertEqual(routing["public_profiles"][FIRST], "fast")
        self.assertNotEqual(FIRST, SECOND)

    def test_attempts_ask_two_providers_at_once_then_hedge(self) -> None:
        self.assertEqual(
            request_router.attempts(),
            (
                (FIRST, "router", False),
                (FIRST, "router_alt", True),
                (FIRST, "router", False),
            ),
        )
        self.assertEqual(
            request_router.payload("hello", [], FIRST, 2)["text_role"], "router"
        )
        self.assertNotIn(
            "text_role", request_router.payload("hello", [], SECOND, 2, "profile")
        )
        routing = yaml.safe_load(
            Path("services/model-gateway/routing.yaml").read_text()
        )
        prices = yaml.safe_load(
            Path("services/model-gateway/pricing.yaml").read_text()
        )["models"]
        serverless = routing["serverless"]
        models = {serverless[role]["model"] for role in ("router", "router_alt")}
        self.assertEqual(len(models), 2)
        self.assertNotIn(serverless["fast"]["model"], models)
        self.assertTrue(models <= set(prices))
        # Two providers: the default one and a declared preset.
        self.assertNotIn("provider", serverless["router"])
        self.assertEqual(serverless["router_alt"]["provider"], "mistral")

    def test_owned_inference_makes_one_call(self) -> None:
        with patch("packages.privacy.owned_only", return_value=True):
            self.assertEqual(request_router.attempts(), ((FIRST, "router", False),))

    def test_payload_turns_reasoning_off_and_makes_one_attempt(self) -> None:
        payload = request_router.payload("hello", [], SECOND, 3.5)
        self.assertEqual(payload["profile"], SECOND)
        self.assertEqual(payload["timeout"], 3.5)
        self.assertEqual(payload["reasoning_effort"], "none")
        self.assertEqual(payload["reasoning_ceiling"], "none")
        self.assertTrue(payload["reasoning_disabled"])
        self.assertTrue(payload["single_attempt"])
        self.assertEqual(payload["max_tokens"], 200)

    def test_gateway_accepts_the_payload(self) -> None:
        import sys

        sys.path.insert(0, "services/model-gateway")
        from agent_provider import AgentRequest

        for name, role, _ in request_router.attempts():
            request = AgentRequest.model_validate(
                request_router.payload("hello", [], name, 4, role)
            )
            self.assertEqual(request.text_role, role)


class RetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_providers_are_asked_at_once_and_the_first_wins(self) -> None:
        gateway = Recorded(
            router=(0.3, RECORDED_ANSWER), router_alt=(0.05, RECORDED_FILE)
        )
        with router("2"):
            started = time.monotonic()
            routed, _, cost, reason = await request_router.classify(
                "x", [], complete=gateway
            )
        self.assertEqual(routed, Decision(kind="file", file_format="docx"))
        self.assertEqual(reason, "")
        self.assertEqual(
            [name for name, _, _ in gateway.asked], ["router", "router_alt"]
        )
        self.assertLess(gateway.asked[1][1] - gateway.asked[0][1], 0.05)
        self.assertLess(time.monotonic() - started, 0.25)
        self.assertEqual(cost, Decimal("0.0008876"))

    async def test_late_calls_are_hedged_on_the_first_role(self) -> None:
        gateway = Recorded(
            router=(0.6, RECORDED_FILE), router_alt=(30, RECORDED_ANSWER)
        )
        with router("3", hedge_ms="400"):
            started = time.monotonic()
            routed, milliseconds, _, reason = await request_router.classify(
                "x", [], complete=gateway
            )
            elapsed = time.monotonic() - started
        self.assertEqual(routed, Decision(kind="file", file_format="docx"))
        self.assertEqual(reason, "")
        names = [name for name, _, _ in gateway.asked]
        self.assertEqual(names, ["router", "router_alt", "router"])
        at = [moment for _, moment, _ in gateway.asked]
        # The hedge is sent one delay after the first pair, with the time left.
        self.assertAlmostEqual(at[2] - at[0], 0.4, delta=0.15)
        self.assertAlmostEqual(gateway.asked[0][2], 3.0, delta=0.05)
        self.assertAlmostEqual(gateway.asked[2][2], 2.6, delta=0.15)
        # The first call wins at 0.6 s, before the hedge would.
        self.assertLess(elapsed, 0.9)
        self.assertLess(milliseconds, 900)

    async def test_quick_failures_send_the_next_call_at_once(self) -> None:
        gateway = Recorded(
            router=(0, RuntimeError("gateway_unavailable")),
            router_alt=(0, RuntimeError("gateway_unavailable")),
        )
        with router("4"):
            started = time.monotonic()
            routed, _, _, reason = await request_router.classify(
                "x", [], complete=gateway
            )
        self.assertIsNone(routed)
        self.assertEqual(reason.count("+"), 2)
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertEqual(len(gateway.asked), 3)

    async def test_three_timeouts_stay_within_the_bound(self) -> None:
        gateway = Recorded(router=(30, RECORDED_FILE), router_alt=(30, RECORDED_FILE))
        with router("2"):
            started = time.monotonic()
            routed, _, _, reason = await request_router.classify(
                "x", [], complete=gateway
            )
        self.assertIsNone(routed)
        self.assertEqual(reason, "timeout+timeout+timeout")
        self.assertLess(time.monotonic() - started, 2.3)


class FallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeouts_keep_the_requested_file_in_five_languages(self) -> None:
        for language, (text, extension) in DELIVERABLES.items():
            with self.subTest(language=language):
                gateway = Recorded(
                    router=(30, RECORDED_FILE), router_alt=(30, RECORDED_FILE)
                )
                req = request(text)
                with (
                    router("0.5", minimum=0.1, hedge_ms="100"),
                    patch.object(request_router, "_gateway", gateway),
                    self.assertLogs(
                        "services.orchestrator.request_router", "WARNING"
                    ) as logs,
                ):
                    routed = await request_router.route(req)
                    self.assertEqual(request_router.decision(req), routed)
                self.assertIsNotNone(routed)
                assert routed is not None
                self.assertEqual((routed.kind, routed.file_format), ("file", extension))
                self.assertTrue(routed.work)
                self.assertEqual(len(gateway.asked), 3)
                line = logs.output[0]
                self.assertIn("kind=file source=local", line)
                self.assertIn(f"format={extension}", line)
                self.assertIn("fallback=timeout+timeout+timeout", line)
                self.assertIn("attempts=3", line)
                self.assertEqual(req.__dict__[COST_KEY], Decimal(0))
                # Never the user's text.
                self.assertNotIn(text.split()[-1], line)

    async def test_timeouts_leave_other_turns_to_the_local_classifier(self) -> None:
        for text, kind in (
            ("what's a good name for a grey cat?", "answer"),
            ("écris un petit mot pour remercier ma voisine", "answer"),
            ("résume https://example.org/rapport.pdf", "answer"),
            ("connecte-toi à mon serveur en ssh et convertis le csv", "remote"),
            ("fé moi un jeu de mémoire avec des cartes", "app"),
            ("lance le script stp", "run"),
        ):
            with self.subTest(text=text):
                gateway = Recorded(
                    router=(30, RECORDED_FILE), router_alt=(30, RECORDED_FILE)
                )
                with (
                    router("0.5", minimum=0.1, hedge_ms="100"),
                    patch.object(request_router, "_gateway", gateway),
                    self.assertLogs(
                        "services.orchestrator.request_router", "WARNING"
                    ) as logs,
                ):
                    routed = await request_router.route(request(text))
                self.assertEqual(routed.kind if routed else None, kind)
                self.assertIn("source=local", logs.output[0])

    async def test_a_model_decision_is_never_replaced(self) -> None:
        gateway = Recorded(
            router=(0, RECORDED_ANSWER), router_alt=(0.2, RECORDED_ANSWER)
        )
        req = request("What is the difference between a PDF and a Word file?")
        with (
            router("2"),
            patch.object(request_router, "_gateway", gateway),
            self.assertLogs("services.orchestrator.request_router", "WARNING") as logs,
        ):
            self.assertEqual(await request_router.route(req), Decision(kind="answer"))
        self.assertIn("source=model", logs.output[0])
        self.assertIn("attempts=2", logs.output[0])


class NamedFormatTests(unittest.TestCase):
    def test_formats_and_their_names(self) -> None:
        cases = {
            "un CV en PDF": ("pdf",),
            "Schreib mir ein Word-Dokument": ("docx",),
            "a word document with the minutes": ("docx",),
            "le compte rendu au format word": ("docx",),
            "export it as notes.md": ("md",),
            "the table as CSV please": ("csv",),
            "una presentación de 5 diapositivas": ("pptx",),
            "un foglio di calcolo per le spese": ("xlsx",),
            "a slide deck for Monday": ("pptx",),
            "the PDF and a PowerPoint": ("pptx", "pdf"),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(request_router.named_formats(text), expected)
        both = request_router.deliver_fallback("the PDF and a PowerPoint")
        self.assertEqual(both, Decision(kind="file"))

    def test_no_format_named(self) -> None:
        for text in (
            "in a word, what is love?",
            "write a word of thanks to my colleague",
            "summarise https://example.org/report.pdf",
            "read www.example.org/data.csv",
            "e-mail me the summary",
            "how are you",
            "",
        ):
            with self.subTest(text=text):
                self.assertEqual(request_router.named_formats(text), ())
                self.assertIsNone(request_router.deliver_fallback(text))

    def test_long_inputs_stay_linear(self) -> None:
        for text in (
            "a-" * 50_000,
            "document " * 20_000,
            "in " + " " * 100_000 + "x",
            "x" * 100_000 + ".m",
            "Word" * 50_000,
        ):
            started = time.perf_counter()
            request_router.named_formats(text)
            self.assertLess(time.perf_counter() - started, 1.0, text[:12])


class LexicalGateTests(unittest.TestCase):
    def test_a_file_asked_before_the_question_words_is_a_file(self) -> None:
        for text in (
            "Make a Word document explaining how to repot an orchid, with an "
            "illustration for each step",
            "Crée un PDF qui explique pourquoi le ciel est bleu",
            "Erstelle eine PowerPoint, die erklärt, wie eine Wärmepumpe funktioniert",
        ):
            with self.subTest(text=text):
                self.assertEqual(work_reason(text), "file")

    def test_a_question_about_making_a_file_stays_a_question(self) -> None:
        for text in (
            "How do I make a PDF from a Word document?",
            "Explique-moi comment créer un tableau Excel",
            "what is the difference between docx and pdf",
        ):
            with self.subTest(text=text):
                self.assertIsNone(work_reason(text))


if __name__ == "__main__":
    unittest.main()
