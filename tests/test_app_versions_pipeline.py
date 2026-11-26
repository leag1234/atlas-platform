"""A five-turn app conversation through the chat pipeline keeps one artifact.

Staging -148 (2026-11-21), multi-turn battery: the answer of the first turn
lost its link to version 1, so the next turn found no current app and created
a second artifact; the later answers named versions 2 and 3 of an artifact
the conversation had never shown. The first turn's request ("une appli quiz
des capitales europeennes") was held for the unsourced details review, whose
rewrite of the app's answer dropped the code-written footer. Earlier staging
records show the same loss before the expectations ledger (02:38, 03:11).

This runs the battery's scenario through chat_pipeline.process, with that
review active and rewriting as it did: one artifact, versions 1 to 4, every
answer linking the version it stored, and the expectations ledger growing
across the turns.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import unittest
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, patch

from services.orchestrator import artifact_store, chat_pipeline, expectations
from services.orchestrator.chat_schema import ChatRequest
from services.orchestrator.interactions import Interaction
from services.orchestrator.loop import Message, Reservation, Turn
from services.orchestrator.unsourced_details import Report

OWNER = "quiz-player@example.test"
LINK = re.compile(r"/api/atlas/artifacts/([0-9a-f]{32})/versions/(\d+)/open")


def quiz(*, timer: bool = False, flags: bool = False) -> str:
    timer_html = '<div id="timer">10 s</div>' if timer else ""
    flags_html = (
        '<svg id="flag" viewBox="0 0 3 2"><rect width="3" height="2" fill="#0055a4"/></svg>'
        if flags
        else ""
    )
    timer_js = (
        "let left = 10; setInterval(() => { left = Math.max(0, left - 1); "
        "document.getElementById('timer').textContent = left + ' s'; }, 1000);\n"
        if timer
        else ""
    )
    return f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8"><title>Capitales d'Europe</title></head>
<body><h1>Capitales d'Europe</h1>{flags_html}{timer_html}
<p id="question">Quelle est la capitale du Portugal ?</p>
<button id="a">Lisbonne</button><button id="b">Porto</button>
<p id="score">Score : 0</p>
<script>
let score = 0;
document.getElementById('a').addEventListener('click', () => {{
  score += 1; document.getElementById('score').textContent = 'Score : ' + score;
}});
{timer_js}</script></body></html>"""


def answer(code: str) -> str:
    return f"```html\n{code}\n```\nUn quiz des capitales européennes avec un score."


TURNS: list[tuple[str, str, dict[str, Any] | None]] = [
    (
        "fais moi une appli quiz des capitales européennes avec un score",
        answer(quiz()),
        {
            "new": [
                {"statement": "Quiz des capitales européennes"},
                {"statement": "Un score"},
            ]
        },
    ),
    (
        "ajoute un chrono de 10 secondes par question",
        answer(quiz(timer=True)),
        {"new": [{"statement": "Chrono de 10 secondes par question"}]},
    ),
    ("au fait comment on dit bonne chance en allemand ?", "", None),
    (
        "ajoute les drapeaux des pays",
        answer(quiz(timer=True, flags=True)),
        {"new": [{"statement": "Drapeaux des pays"}]},
    ),
    (
        "ça marche pas, rien ne s'affiche",
        answer(quiz(timer=True, flags=True).replace("Score : 0", "Score : 0 ")),
        {"new": []},
    ),
]


class FakeModel:
    """The GatewayModel surface an app turn uses, one scripted file per turn."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.tools: list[Message] = []
        self.sink: Any = None
        self.observing = False
        self.local_enabled = False
        self.reasoning_ceiling: str | None = None
        self.reasoning_room = 0
        self.cost_ceiling: Decimal | None = None
        self.provider_retries = 0
        self.input_tokens = self.output_tokens = 0
        self.provider_model = "fake"
        self.reasoning = ""
        self.trace_tokens = self.answer_tokens = 0
        self.token_split_estimated = self.reasoning_retried = False
        self.reasoning_used = "none"
        self.generation_ms = 0.0
        self.spent = Decimal(0)
        self.observations: list[object] = []

    def configure_quality(self, profile: str, max_tokens: int, **flags: object) -> None:
        pass

    async def complete(self, messages: list[Message], timeout: float) -> Turn:
        self.spent += Decimal("0.01")
        return Turn(self.text, (), Reservation(100, Decimal("0.01")))


class PipelineBase(unittest.IsolatedAsyncioTestCase):
    """chat_pipeline.process with the details review rewriting as on staging."""

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        environment = patch.dict(
            os.environ,
            {
                "ATLAS_DATA_DIR": self.directory.name,
                "ATLAS_ARTIFACTS": "1",
                "ATLAS_APP_RENDER": "off",
                "ATLAS_EXPECTATIONS": "1",
                "ATLAS_GROUNDING": "on",
                "ATLAS_INTERACTION_DIR": self.directory.name,
            },
        )
        environment.start()
        self.addCleanup(environment.stop)
        self.extraction = 0
        self.reviewed: list[str] = []

    async def ledger_call(
        self, payload: dict[str, Any], seconds: float
    ) -> dict[str, Any]:
        data = json.loads(payload["messages"][1]["content"])
        if "latest_message" in data:
            update = next(
                (turn[2] for turn in TURNS if turn[0] == data["latest_message"]), None
            )
            return {"text": json.dumps(update or {"new": []})}
        return {
            "text": json.dumps(
                {
                    "results": [
                        {"id": r["id"], "met": True} for r in data["requirements"]
                    ]
                }
            )
        }

    async def rewriting_review(
        self, text: str, *args: Any, **kwargs: Any
    ) -> tuple[str, Report]:
        """The details review as it ran on staging: a rewrite that dropped the
        code-written footer, and one encyclopedia article read."""
        self.reviewed.append(text)
        report = Report()
        report.reference_urls = ["https://fr.wikipedia.org/wiki/Example"]
        kept = "\n".join(
            line for line in text.splitlines() if "/api/atlas/" not in line
        )
        return kept, report

    async def app_turn(
        self, history: list[dict[str, str]], text: str, file: str
    ) -> Interaction:
        request = ChatRequest.model_validate(
            {"messages": [*history, {"role": "user", "content": text}], "lang": "fr"}
        )
        request.__dict__["artifact_owner"] = OWNER
        request.__dict__["daily_user"] = OWNER
        item = Interaction()
        with (
            patch(
                "services.orchestrator.app_turn.GatewayModel.connect",
                AsyncMock(return_value=FakeModel(file)),
            ),
            # Staging routes with the model router; its decision for these
            # messages: a new app, then changes of the current one.
            patch(
                "services.orchestrator.chat_pipeline.app_intent",
                return_value="modify" if history else "create",
            ),
            patch("services.orchestrator.expectations._complete", self.ledger_call),
            patch("services.orchestrator.unsourced_details.mode", return_value="on"),
            patch(
                "services.orchestrator.unsourced_details.wants_review",
                return_value=True,
            ),
            patch(
                "services.orchestrator.unsourced_details.review", self.rewriting_review
            ),
        ):
            await chat_pipeline.process(request, item)
        return item


class AppVersionsThroughThePipeline(PipelineBase):
    async def test_one_artifact_four_versions_and_a_cumulative_ledger(self) -> None:
        history: list[dict[str, str]] = []
        artifact_ids: set[str] = set()
        versions: list[int] = []
        actives: list[int] = []
        for text, file, _ in TURNS:
            if not file:
                # A plain chat turn: no app turn runs, no version is stored.
                history += [
                    {"role": "user", "content": text},
                    {"role": "assistant", "content": "On dit « Viel Glück! »."},
                ]
                continue
            item = await self.app_turn(history, text, file)
            self.assertEqual(item.task_type, "artifact", item.reponse)
            stored = item.artifacts[0]
            linked = LINK.findall(item.reponse)
            # The answer links exactly the version it stored.
            self.assertIn(
                (str(stored["id"]), str(stored["version"])), linked, item.reponse
            )
            artifact_ids.add(str(stored["id"]))
            versions.append(int(stored["version"]))
            ledger = expectations.load(f"app:{stored['id']}", int(stored["version"]))
            assert ledger is not None
            actives.append(len(ledger[0].active()))
            history += [
                {"role": "user", "content": text},
                {"role": "assistant", "content": item.reponse},
            ]
        self.assertEqual(len(artifact_ids), 1)
        self.assertEqual(versions, [1, 2, 3, 4])
        self.assertEqual(artifact_store.read(artifact_ids.pop(), OWNER).version, 4)
        self.assertEqual(actives, [2, 3, 4, 4])
        # An app's answer is not a factual text: the details review never ran on it.
        self.assertEqual(self.reviewed, [])


ORG = "6f1c2d3e-4a5b-4c6d-8e7f-0a1b2c3d4e5f"
PROJECT = "0e9d8c7b-6a5f-4e3d-9c2b-1a0f9e8d7c6b"
RECEIPT = (
    f"[Télécharger rapport-marche.docx](/api/atlas/orgs/{ORG}/projects/{PROJECT}"
    f"/deliverables/d-41/download) · [Aperçu](/api/atlas/orgs/{ORG}/projects/"
    f"{PROJECT}/deliverables/d-41/preview)"
)


class CodeTextSurvivesRewrites(PipelineBase):
    """No bounded rewrite drops what code wrote in an answer."""

    async def test_a_document_receipt_survives_the_details_review(self) -> None:
        async def answered(request: ChatRequest, item: Interaction) -> None:
            item.task_type = "chat"
            item.state = "done"
            item.reponse = (
                "Le rapport sur le marché de Port-Claret fait une page : horaires, "
                "producteurs et accès.\n\n" + RECEIPT
            )

        request = ChatRequest.model_validate(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": "fais un rapport d'une page sur le marché",
                    }
                ],
                "lang": "fr",
            }
        )
        item = Interaction()
        with (
            patch.object(chat_pipeline, "_process_request", answered),
            patch("services.orchestrator.unsourced_details.mode", return_value="on"),
            patch(
                "services.orchestrator.unsourced_details.wants_review",
                return_value=True,
            ),
            patch(
                "services.orchestrator.unsourced_details.review", self.rewriting_review
            ),
        ):
            await chat_pipeline.process(request, item)
        # The review ran and rewrote the prose, and the receipt is back verbatim.
        self.assertEqual(len(self.reviewed), 1)
        self.assertIn(RECEIPT, item.reponse)
        self.assertTrue(item.reponse.startswith("Le rapport sur le marché"))

    def test_only_what_code_wrote_is_protected(self) -> None:
        from services.orchestrator import protected_text

        item = Interaction()
        item.artifacts = [{"id": "a" * 32, "version": 2}]
        stored = (
            f"[Ouvrir « Quiz » (version 2)](/api/atlas/artifacts/{'a' * 32}"
            "/versions/2/open#atlas-view=app) · [Télécharger quiz.html]"
            f"(/api/atlas/artifacts/{'a' * 32}/versions/2/download)"
        )
        invented = f"[version 5](/api/atlas/artifacts/{'a' * 32}/versions/5/open)"
        sources = (
            "<details><summary>Sources</summary>\n- <https://example.org/a>\n</details>"
        )
        trip = "| Départ | Arrivée |\n|---|---|\n| 08:12 | 09:40 |"
        answer = f"Texte.\n\n{trip}\n\n{stored}\n\n{invented}\n\n{sources}"
        kept = protected_text.segments(answer, item, [trip])
        self.assertEqual(kept.parts, [trip, stored])
        restored = protected_text.restore("Texte réécrit.", kept, "test")
        self.assertEqual(restored, f"Texte réécrit.\n\n{trip}\n\n{stored}\n\n{sources}")
        # A source list the rewrite extended is not doubled; nothing is added
        # when everything is still there.
        extended = sources.replace(
            "</details>", "- <https://example.org/b>\n</details>"
        )
        self.assertEqual(
            protected_text.restore(
                f"T.\n\n{trip}\n\n{stored}\n\n{extended}", kept, "test"
            ),
            f"T.\n\n{trip}\n\n{stored}\n\n{extended}",
        )


if __name__ == "__main__":
    unittest.main()
