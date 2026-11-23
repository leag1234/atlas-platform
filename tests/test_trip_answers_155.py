"""The person's register wins over the model's in a trip plan (staging -155).

Staging -155 (2026-11-22, a regional train trip through the web UI): the person wrote
in the familiar form ("... tu me trouves les trains du matin ...
depuis chez moi"), the model opened "depuis votre position", and the code's
block kept "votre position" and "vérifiez-les": the model's own words were
read first, so its polite drift decided the block's register. The person's
messages now decide first (latest first, their own words only); the model's
words decide only when the person's say nothing. The plan's instruction tells
the answer model the person's register.

Every street, stop and service below is fictional.
"""

from typing import Any

from services.orchestrator import transit_planner as tp
from services.orchestrator import travel_claims
from test_register_and_markers import POLITE_LEFT
from test_trip_answers_152 import Case, Planner, plan, train

# The shape of the -155 question, with a fictional place.
QUESTION = (
    "Demain je dois être à la Gare Centrale Fictive pour 12h30, tu me trouves "
    "les trains du matin qui y arrivent à temps, depuis chez moi"
)
# The model's drift: its opening and closing in the polite form.
OPENING = (
    "Voici les horaires possibles demain pour arriver à la Gare Centrale "
    "Fictive à 12:30, depuis votre position à Villenord :"
)
CLOSING = "Je vous conseille la deuxième option si vous voulez de la marge."


def plan_block(case: Case, language: str = "fr") -> str:
    """A computed block from the device position (fictional trip)."""
    data = case.planned(
        Planner(plan(train("08:00", "10:00", "Matin Fictif 1"))), language
    )
    return str(data["rendered"]).replace(
        "Rue Inventée 3 (Villenord)", tp.words(language)["position"]
    )


class PersonFirstTest(Case):
    def block(self, language: str = "fr") -> str:
        return plan_block(self, language)

    def turn(self, block: str, *asked: str) -> travel_claims.Turn:
        turn = travel_claims.Turn(
            active=True, language="fr", zone="Europe/Paris", asked=list(asked)
        )
        turn.plan = block
        return turn

    def test_the_familiar_question_wins_over_a_polite_answer(self) -> None:
        block = self.block()
        self.assertIn("votre position", block)
        turn = self.turn(block, QUESTION)
        answer = OPENING + "\n\n" + block + "\n\n" + CLOSING
        final = tp.planned_answer(answer, turn)
        styled = final.replace(OPENING, "").replace(CLOSING, "")
        self.assertIn("ta position", styled)
        self.assertIn("vérifie-les auprès de l’exploitant", styled)
        self.assertIsNone(POLITE_LEFT["fr"].search(styled), styled)
        # The block is given back once, and later steps find it as given.
        self.assertEqual(turn.plan, styled.strip())

    def test_the_latest_message_that_says_one_decides(self) -> None:
        block = self.block()
        for asked, familiar in (
            # Latest first: an earlier polite message gives way.
            (("Pouvez-vous m'aider ?", QUESTION), True),
            ((QUESTION, "Donnez-moi plutôt les trains de l'après-midi"), False),
            # The latest says nothing: the earlier one does.
            ((QUESTION, "et demain matin ?"), True),
        ):
            with self.subTest(asked=asked):
                final = tp.planned_answer(
                    OPENING + "\n\n" + block, self.turn(block, *asked)
                )
                self.assertEqual("ta position" in final, familiar, final)

    def test_without_a_register_from_the_person_the_model_decides(self) -> None:
        block = self.block()
        neutral = "comment j'y vais demain depuis chez moi ?"
        for opening, familiar in (
            ("Voici ton trajet depuis chez toi :", True),
            ("Voici votre trajet depuis chez vous :", False),
        ):
            with self.subTest(opening=opening):
                final = tp.planned_answer(
                    opening + "\n\n" + block, self.turn(block, neutral)
                )
                self.assertEqual("ta position" in final, familiar, final)

    def test_pasted_text_is_not_the_persons_register(self) -> None:
        self.assertEqual(tp.person_register([QUESTION], "fr"), "informal")
        self.assertEqual(tp.person_texts(["a", " ", "b"]), ["b", "a"])

    def test_the_instruction_names_the_persons_register(self) -> None:
        expected = {
            ("fr", QUESTION): "with 'tu', never 'vous'",
            ("fr", "Pouvez-vous me donner les trains ?"): "with 'vous', never 'tu'",
            ("de", "kannst du mir die Züge sagen?"): "with 'du', never 'Sie'",
            ("es", "¿puedes darme los trenes?"): "with 'tú', never 'usted'",
            ("it", "puoi dirmi i treni?"): "with 'tu', never 'Lei'",
        }
        for (language, asked), sentence in expected.items():
            with self.subTest(language=language, asked=asked):
                self.assertIn(sentence, tp.address_instruction([asked], language))
        self.assertEqual(tp.address_instruction(["trains tomorrow?"], "en"), "")
        self.assertEqual(
            tp.address_instruction(["comment j'y vais depuis chez moi ?"], "fr"), ""
        )

    def test_the_tool_result_tells_the_answer_model(self) -> None:
        turn = travel_claims.Turn(
            active=True, language="fr", zone="Europe/Paris", asked=[QUESTION]
        )
        planner = Planner(plan(train("08:00", "10:00", "Matin Fictif 1")))
        arguments: dict[str, Any] = {
            "destination": "Gare Centrale Fictive",
            "origin": "Rue Inventée 3, Villenord",
            "arrive_by": "2026-11-20T12:00",
        }
        result = self.run_tool(arguments, planner, turn)
        data = result["data"] if "data" in result else result
        self.assertIn("with 'tu', never 'vous'", data["instruction"])


class ContinuationTest(Case):
    """The location flow resumes the answer: Open WebUI sends the assistant's
    first turn back and the service appends its continuation instruction as a
    user message."""

    def request(self, language: str = "fr") -> Any:
        from services.orchestrator.chat_schema import ChatRequest

        return ChatRequest.model_validate(
            {
                "model": "atlas-qwen",
                "ui_locale": language,
                "messages": [
                    {"role": "user", "content": QUESTION},
                    {
                        "role": "assistant",
                        "content": "Pour répondre, il me faut la position de "
                        "l'appareil : je la demande.",
                    },
                ],
            }
        )

    def test_the_continuation_instruction_is_not_the_persons_words(self) -> None:
        from packages import addressing

        request = self.request()
        self.assertTrue(request.continuation)
        self.assertEqual(request.person_texts, [QUESTION])
        token = addressing.begin(request.person_texts, "fr")
        try:
            self.assertEqual(addressing.turn_register("fr"), "informal")
        finally:
            addressing.end(token)
        # The trip block of that turn follows the person.
        block = plan_block(self)
        turn = travel_claims.Turn(
            active=True, language="fr", zone="Europe/Paris", asked=request.person_texts
        )
        turn.plan = block
        final = tp.planned_answer("Voici les horaires :\n\n" + block, turn)
        self.assertIn("vérifie-les", final)
        self.assertNotIn("vérifiez-les", final)

    def test_the_continuation_instruction_says_no_form_of_address(self) -> None:
        from services.orchestrator.chat_schema import CONTINUATION_INSTRUCTIONS

        for language, text in CONTINUATION_INSTRUCTIONS.items():
            with self.subTest(language=language):
                self.assertEqual(tp.register(text, language), "")
                if language in POLITE_LEFT:
                    self.assertIsNone(POLITE_LEFT[language].search(text), text)
