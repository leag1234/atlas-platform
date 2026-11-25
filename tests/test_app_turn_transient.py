"""A transient provider failure during an app or drawing write is retried
within the turn's time, without using one of the writes; a failure sentence
names its cause (owner rule: a change not applied says why)."""

from __future__ import annotations

import time
import unittest
from typing import Any, cast
from unittest.mock import patch

from packages.artifact_html import script_syntax_location
from packages.user_errors import app_failure
from services.orchestrator import app_turn
from services.orchestrator.app_turn import Progress, Retries, _complete, failure_cause
from services.orchestrator.interactions import Interaction
from services.orchestrator.loop import GatewayError, Turn


class FlakyModel:
    def __init__(self, failures: list[str]) -> None:
        self.failures = list(failures)
        self.calls = 0

    async def complete(self, messages: Any, seconds: float) -> Turn:
        self.calls += 1
        if self.failures:
            raise GatewayError(self.failures.pop(0), 502)
        return Turn(text="<!doctype html><title>ok</title>")


class TransientRetries(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.item = Interaction()
        self.progress = Progress(None, "app", "en")

    async def run_with(
        self, model: FlakyModel, retries: Retries, seconds: float = 200.0
    ) -> Turn:
        with patch.object(app_turn, "TRANSIENT_WAITS", (0.0, 0.0, 0.0)):
            return await _complete(
                cast(Any, model),
                [],
                time.monotonic() + seconds,
                self.progress,
                self.item,
                retries,
            )

    async def test_broken_streams_are_tried_again(self) -> None:
        model = FlakyModel(["incomplete_stream", "provider_error"])
        retries = Retries()
        turn = await self.run_with(model, retries)
        self.assertIn("<title>ok</title>", turn.text)
        self.assertEqual((model.calls, retries.used), (3, 2))

    async def test_retries_are_counted_for_the_whole_turn(self) -> None:
        retries = Retries(used=3)
        with self.assertRaises(GatewayError):
            await self.run_with(FlakyModel(["gateway_unavailable"]), retries)

    async def test_a_lasting_refusal_is_not_retried(self) -> None:
        model = FlakyModel(["content_refused"])
        with self.assertRaises(GatewayError):
            await self.run_with(model, Retries())
        self.assertEqual(model.calls, 1)

    async def test_no_retry_when_the_turn_has_no_time_left(self) -> None:
        model = FlakyModel(["provider_error"])
        with self.assertRaises(GatewayError):
            await self.run_with(model, Retries(), seconds=app_turn.DEADLINE_MARGIN + 20)
        self.assertEqual(model.calls, 1)


class FailureCause(unittest.TestCase):
    def test_causes(self) -> None:
        self.assertEqual(failure_cause("artifact_invalid"), "checks")
        self.assertEqual(failure_cause("incomplete_stream"), "provider")
        self.assertIsNone(failure_cause("content_refused"))

    def test_sentence_names_the_cause_in_each_language(self) -> None:
        for language, word in (
            ("fr", "modèle"),
            ("de", "Modelldienst"),
            ("es", "modelo"),
            ("it", "modello"),
            ("en", "model"),
        ):
            with self.subTest(language=language):
                text = app_failure(
                    "app", language, unchanged_version=4, cause="provider"
                )
                self.assertIn(word, text)
                self.assertIn("4", text)

    def test_no_cause_keeps_the_sentence(self) -> None:
        self.assertEqual(
            app_failure("drawing", "en", unchanged_version=2),
            app_failure("drawing", "en", unchanged_version=2, cause=None),
        )


class SyntaxLocation(unittest.TestCase):
    def test_line_of_the_error_is_given(self) -> None:
        found = script_syntax_location(
            "let total = 1;\nfunction add( {\n  return 2;\n}\n"
        )
        if found is None:
            self.skipTest("node not available")
        message, line = found
        self.assertTrue(message)
        self.assertGreaterEqual(line, 1)


if __name__ == "__main__":
    unittest.main()
