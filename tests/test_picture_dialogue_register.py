"""Questions and reviews about the conversation's picture are written in the
person's register: a conversation in the familiar form read the review in
the polite one (a question about a picture already shown was answered with
"vous" after a request with "tu"). The reading call and the review get the
register sentence every answer path gives (packages.addressing), derived
from the whole conversation: the question itself often says nothing.

Fictional pictures and wordings, three languages, both registers."""

from __future__ import annotations

import json
from typing import Any

from packages import addressing
from services.orchestrator.interactions import Interaction
from services.orchestrator.pictures import dialogue
from test_picture_cpu import FakeServer, rewrite_reply
import test_picture_followups as followups
from test_picture_followups import DialogueGateway, follow_reply
from test_picture_turn import Request

# (language, picture request, question, register, own form, other form,
#  a review answer in the person's form)
CASES = (
    (
        "fr",
        "dessine une trottinette orange avec trois roues",
        "elle a combien de roues ?",
        "informal",
        "tu",
        "vous",
        "Elle a deux roues alors que tu en voulais trois.",
    ),
    (
        "fr",
        "dessinez une trottinette orange avec trois roues",
        "combien de roues a-t-elle ?",
        "formal",
        "vous",
        "tu",
        "Elle a deux roues alors que vous en vouliez trois.",
    ),
    (
        "de",
        "zeichne mir ein gelbes Segelboot mit zwei Segeln",
        "wie viele Segel hat es?",
        "informal",
        "du",
        "Sie",
        "Es hat ein Segel, obwohl du zwei wolltest.",
    ),
    (
        "de",
        "Könnten Sie ein gelbes Segelboot mit zwei Segeln zeichnen?",
        "wie viele Segel hat es?",
        "formal",
        "Sie",
        "du",
        "Es hat ein Segel, obwohl Sie zwei wollten.",
    ),
    (
        "es",
        "dibuja un tranvía verde con cuatro vagones",
        "¿cuántos vagones tiene?",
        "informal",
        "tú",
        "usted",
        "Tiene tres vagones, aunque tú querías cuatro.",
    ),
    (
        "es",
        "dibuje un tranvía verde con cuatro vagones",
        "¿cuántos vagones tiene?",
        "formal",
        "usted",
        "tú",
        "Tiene tres vagones, aunque usted quería cuatro.",
    ),
)
FIRST = "An orange object drawn on a plain light background."


def sentence(own: str, other: str) -> str:
    return (
        f"The person addresses you with '{own}': in every sentence you write to "
        f"them, address them with '{own}', never '{other}'."
    )


class PersonRegisterTests(followups.Base):
    """The register comes from the whole conversation, latest message first."""

    def test_a_question_without_a_mark_takes_the_earlier_register(self) -> None:
        for language, ask, question, found, *_ in CASES:
            with self.subTest(language=language, ask=ask):
                request = Request(question, [("user", ask), ("assistant", "…")])
                self.assertEqual(dialogue.person_register(request, language), found)

    def test_the_latest_message_that_says_one_wins(self) -> None:
        request = Request(
            "vous pouvez me dire combien de roues elle a ?",
            [("user", "dessine une trottinette"), ("assistant", "…")],
        )
        self.assertEqual(dialogue.person_register(request, "fr"), "formal")

    def test_the_persons_own_messages_are_read_when_the_request_has_them(
        self,
    ) -> None:
        # ChatRequest.person_texts leaves out the service's continuation.
        request = Request("Continue where you stopped, address them as vous.")
        request.person_texts = ["dessine une trottinette", "combien de roues ?"]  # type: ignore[attr-defined]
        self.assertEqual(dialogue.person_register(request, "fr"), "informal")

    def test_no_register_leaves_the_instructions_as_written(self) -> None:
        messages = [
            {"role": "system", "content": dialogue.REVIEW_SYSTEM},
            {"role": "user", "content": "{}"},
        ]
        for language, ask, question in (
            ("en", "draw a green kite", "how many tails does it have?"),
            ("fr", "une trottinette orange", "combien de roues ?"),
        ):
            with self.subTest(language=language):
                request = Request(question, [("user", ask), ("assistant", "…")])
                self.assertEqual(
                    dialogue.in_register(messages, request, language), messages
                )


class PictureQuestionRegisterTests(followups.Base):
    """A question about the picture shown, through the picture dialogue."""

    async def follow(
        self, gateway: DialogueGateway, history: list[tuple[str, str]], text: str
    ) -> tuple[bool, Interaction]:
        # Same turn helper as the follow-up tests, on the same fixtures.
        helper: Any = followups.FollowUpTests.follow
        result: tuple[bool, Interaction] = await helper(self, gateway, history, text)
        return result

    async def ask(
        self, language: str, ask: str, question: str, answer: str
    ) -> tuple[DialogueGateway, Interaction]:
        review = json.dumps(
            {"answer": answer, "shown": [], "missing": [], "other": [], "plan": []}
        )
        gateway = DialogueGateway(follow_reply("review", requirements=[]), review)
        gateway.reply = rewrite_reply(prompt=FIRST, language=language)
        server = FakeServer()
        first = await self.run_turn(gateway, server, ask)
        history = [("user", ask), ("assistant", first.reponse)]
        # As in production: the turn's words are recorded (chat_pipeline).
        token = addressing.begin([ask, question], language)
        try:
            done, item = await self.follow(gateway, history, question)
        finally:
            addressing.end(token)
        self.assertTrue(done)
        return gateway, item

    async def test_both_calls_receive_the_persons_register(self) -> None:
        for language, ask, question, found, own, other, answer in CASES:
            with self.subTest(language=language, register=found):
                gateway, item = await self.ask(language, ask, question, answer)
                for calls in (gateway.follows, gateway.reviews):
                    system = str(calls[-1]["messages"][0]["content"])
                    self.assertTrue(system.endswith(sentence(own, other)), system)
                # The model's answer and the code's sentences agree.
                self.assertIn(answer, item.reponse)
                self.assertEqual(addressing.register(item.reponse, language), found)

    async def test_the_question_payload_is_otherwise_unchanged(self) -> None:
        language, ask, question, *_ = CASES[0]
        gateway, _ = await self.ask(language, ask, question, "")
        system = str(gateway.reviews[-1]["messages"][0]["content"])
        self.assertTrue(system.startswith(dialogue.REVIEW_SYSTEM))
        data = json.loads(
            next(
                part["text"]
                for part in gateway.reviews[-1]["messages"][1]["content"]
                if part["type"] == "text"
            )
        )
        self.assertEqual(data["last_message"], question)
