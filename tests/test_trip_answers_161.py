"""One plan per trip answer: the code's block, and the model's advice only
(staging -161).

Staging -161 (Lorvenne to Paris through the web UI): the model summed up the
three options in its own words ("Option 1 : départ 08 h 31, arrivée 11 h 21
(marge d'environ 1 h 09)", their legs with times), advised ("Mon conseil :
l'option 1 (départ 08 h 31) te laisse plus d'une heure de marge ..."), and
closed with a footer of its own naming operators the plan never checked;
the code's block then gave the same options and its own footer. The answer
gave the plan twice and two footers.

The restated options go and the block takes their place (times written
"08 h 31" are clock times of the plan); a sentence that advises stays,
without the times in parentheses; a footer that is the block's note with
other names goes. Every street, stop and service below is fictional.
"""

from typing import Any

from services.orchestrator import transit_planner as tp
from services.orchestrator import travel_claims
from test_trip_answers_152 import Case, Planner, plan, train

QUESTION = (
    "Demain je dois être à la Gare Centrale Fictive vers midi, trouve-moi les "
    "horaires du matin, depuis la rue Inventée 3 a Villenord"
)
# Three trains in UTC: 06:00, 06:30 and 07:10 local are 05:00, 05:30, 06:10.
TRAINS = plan(
    train("05:00", "07:00", "Matin Fictif 1"),
    train("05:30", "07:30", "Matin Fictif 3"),
    train("06:10", "08:45", "Matin Fictif 5"),
)


class Trip(Case):
    def planned_turn(self, language: str = "fr") -> tuple[travel_claims.Turn, str]:
        turn = travel_claims.Turn(
            active=True, language=language, zone="Europe/Paris", asked=[QUESTION]
        )
        arguments: dict[str, Any] = {
            "destination": "Gare Centrale Fictive",
            "origin": "Rue Inventée 3, Villenord",
            "arrive_by": "2026-11-20T12:00",
        }
        result = self.run_tool(arguments, Planner(TRAINS), turn)
        data = result["data"]
        self.assertEqual(len(data["itineraries"]), 3)
        return turn, str(data["rendered"])

    def summary(self) -> str:
        return (
            "Voici les horaires possibles demain pour arriver à la Gare Centrale "
            "Fictive avant 12 h, depuis chez toi :\n\n"
            "**Option 1** : départ 05 h 40, arrivée 08 h 00 (marge de 4 h)\n\n"
            "Marche jusqu'au Quai Nord Fictif, puis Matin Fictif 1 : Quai Nord "
            "Fictif 06 h 00 → Gare Centrale Fictive 08 h 00.\n\n"
            "Option 2 : départ 06 h 10, arrivée 08 h 30\n\n"
            "Matin Fictif 3 (06 h 30) comme l'option 1.\n\n"
            "Option 3 : départ 06 h 50, arrivée 09 h 45\n\n"
            "Mon conseil : l'option 1 (départ 05 h 40) te laisse le plus de "
            "marge, ce qui est plus serein pour un déjeuner. L'option 3 arrive "
            "à 09 h 45, plus tard que les autres.\n\n"
            "Heures locales (Europe/Paris), d'après les horaires publiés ; "
            "vérifie-les auprès de Rail Fictif Connect et de la Régie Fictive "
            "avant de partir."
        )


class OnePlanTest(Trip):
    def test_the_restated_options_and_footer_give_way_to_the_block(self) -> None:
        turn, rendered = self.planned_turn()
        final = tp.planned_answer(self.summary(), turn)
        block = turn.plan
        self.assertIn("vérifie-les auprès de l’exploitant", block)
        # One plan: the block once, in the person's register.
        self.assertEqual(final.count(block), 1, final)
        self.assertEqual(final.count("Option 1"), 1, final)
        for gone in (
            "05 h 40",
            "08 h 00",
            "06 h 30",
            "09 h 45",
            "Marche jusqu'au Quai Nord Fictif",
            "comme l'option 1",
            "Rail Fictif Connect",
            "Régie Fictive",
        ):
            self.assertNotIn(gone, final)
        # One closing note, the block's.
        self.assertEqual(final.count("Heures locales"), 1, final)
        # The opening first, the block in place of the summary, the advice
        # after it, without its time.
        advice = (
            "Mon conseil : l'option 1 te laisse le plus de marge, ce qui est plus "
            "serein pour un déjeuner."
        )
        self.assertIn(advice, final)
        self.assertLess(final.index("depuis chez toi"), final.index(block))
        self.assertLess(final.index(block), final.index(advice))

    def test_an_answer_with_only_an_opening_and_the_block_is_unchanged(self) -> None:
        # The -157 shape: the opening, then the block as code wrote it.
        turn, rendered = self.planned_turn()
        answer = "Voici les horaires possibles demain :\n\n" + rendered
        final = tp.planned_answer(answer, turn)
        self.assertEqual(final, "Voici les horaires possibles demain :\n\n" + turn.plan)

    def test_advice_without_a_time_stays_as_written(self) -> None:
        turn, rendered = self.planned_turn()
        advice = "Je te conseille l'option 2 si tu veux de la marge."
        final = tp.planned_answer(
            "Voici les trains :\n\n" + rendered + "\n\n" + advice, turn
        )
        self.assertTrue(final.endswith(advice), final)

    def test_a_restated_option_in_english_goes_too(self) -> None:
        turn, rendered = self.planned_turn("en")
        answer = (
            "Here are your trains for tomorrow:\n\n"
            "Option 1: leave 5:40, arrive 8:00 (4 h margin)\n\n"
            "Option 2: leave 6:10, arrive 8:30\n\n"
            "My advice: option 1 (leaving 5:40) is the safest, with the most "
            "margin.\n\n"
            "Times are local (Europe/Paris), from published timetables; check "
            "them with Rail Fictif Connect and the Fictive Transit Board before "
            "you travel."
        )
        final = tp.planned_answer(answer, turn)
        self.assertEqual(final.count("Option 1"), 1, final)
        self.assertNotIn("leave 5:40", final)
        self.assertNotIn("leaving 5:40", final)
        self.assertNotIn("Rail Fictif Connect", final)
        self.assertEqual(final.count("Times are local"), 1, final)
        self.assertIn("My advice: option 1 is the safest, with the most margin.", final)

    def test_spaced_clock_times_are_times_and_durations_are_not(self) -> None:
        self.assertEqual(tp._clocks("départ 08 h 31, arrivée 9h04"), {"08:31", "09:04"})
        self.assertEqual(tp._clocks("2 h 50 min de trajet"), set())
        self.assertEqual(tp._clocks("à 12 h 30"), {"12:30"})


class BlockGivenTooTest(Trip):
    """The model gave the block as code wrote it, after its own summary."""

    def test_the_summary_above_an_unaltered_block_goes(self) -> None:
        turn, rendered = self.planned_turn()
        final = tp.planned_answer(self.summary() + "\n\n" + turn.plan, turn)
        block = turn.plan
        self.assertEqual(final.count(block), 1, final)
        self.assertEqual(final.count("Option 1"), 1, final)
        self.assertEqual(final.count("Heures locales"), 1, final)
        self.assertNotIn("Rail Fictif Connect", final)
        self.assertNotIn("05 h 40", final)
        advice = "Mon conseil : l'option 1 te laisse le plus de marge"
        self.assertIn(advice, final)
        self.assertLess(final.index("depuis chez toi"), final.index(block))
        self.assertLess(final.index(block), final.index(advice))

    def test_the_pipeline_checks_give_one_plan(self) -> None:
        # The pipeline takes an unaltered block out before its checks and
        # gives it back after them (chat_pipeline._checked_trip).
        import asyncio
        from unittest.mock import AsyncMock, patch

        from services.orchestrator import chat_pipeline

        turn, rendered = self.planned_turn()
        answer = self.summary() + "\n\n" + rendered
        with patch(
            "services.orchestrator.unsourced_details._complete",
            AsyncMock(return_value=None),
        ):
            final, _ = asyncio.run(
                chat_pipeline._checked_trip(
                    turn, answer, "fr", question=QUESTION, profile="atlas-qwen"
                )
            )
        self.assertEqual(final.count("Option 1"), 1, final)
        self.assertEqual(final.count("Heures locales"), 1, final)
        self.assertNotIn("Rail Fictif Connect", final)
        self.assertNotIn("05 h 40", final)


class CheckNotesRegisterTest(Trip):
    def test_the_notes_of_the_trip_checks_follow_the_person(self) -> None:
        # A sentence naming a place no source gives is cut with a note; in
        # the person's familiar register while the checks run (the pipeline
        # opens the addressing context around them).
        import asyncio
        import inspect
        from unittest.mock import AsyncMock, patch

        from packages import addressing
        from services.orchestrator import chat_pipeline

        turn, rendered = self.planned_turn()
        answer = "Voici les trains depuis la Place Imaginaire Fictive :\n\n" + rendered
        token = addressing.begin([QUESTION], "fr")
        try:
            with patch(
                "services.orchestrator.unsourced_details._complete",
                AsyncMock(return_value=None),
            ):
                final, _ = asyncio.run(
                    chat_pipeline._checked_trip(
                        turn, answer, "fr", question=QUESTION, profile="atlas-qwen"
                    )
                )
        finally:
            addressing.end(token)
        self.assertIn("vérifie lignes", final)
        self.assertNotIn("vérifiez", final)
        source = inspect.getsource(chat_pipeline.process)
        checks = source.index("await _checked_trip(")
        self.assertIn(
            "addressing.begin(request.person_texts", source[checks - 600 : checks]
        )
