"""A margin the answer states for a planned option is the plan's (staging
-183).

Staging -183 (Munich to Berlin, by 14:00): "If you prefer a later start,
Option 2 still gets you there nearly two hours before your 2 pm deadline"
under an option arriving at 12:27, 1 h 33 min before. The sentence was the
model's; the travel checks read durations in figures only and leave a margin
advised to the person alone. Code now computes each option's margin and
checks every margin the model states, in figures or words, with or without
"nearly", "about", "more than", in the five interface languages: a wrong one
is written as computed; one naming no option that fits none goes.

The plan below (fictional) arrives at 08:00, 08:30 and 09:45 for 12:00: its
margins are 4 h, 3 h 30 min and 2 h 15 min.
"""

from services.orchestrator import transit_planner as tp
from test_trip_answers_161 import Trip

OPENING = "Voici les trains pour arriver avant 12 h, depuis chez toi :"


class MarginTest(Trip):
    def final(self, sentence: str, language: str = "fr") -> tuple[str, str]:
        turn, rendered = self.planned_turn(language)
        self.assertEqual([m[0] for m in turn.plan_margins], [240, 210, 135])
        final = tp.planned_answer(OPENING + "\n\n" + rendered + "\n\n" + sentence, turn)
        return final, turn.plan

    def test_a_misstated_margin_is_written_as_computed(self) -> None:
        for sentence, expected in (
            (
                "If you prefer a later start, Option 2 still gets you there nearly "
                "two hours before your noon deadline.",
                "If you prefer a later start, Option 2 still gets you there 3 h 30 "
                "min before your noon deadline.",
            ),
            (
                "L’option 3 te laisse plus de trois heures de marge.",
                "L’option 3 te laisse 2 h 15 min de marge.",
            ),
            (
                "L'opzione 3 arriva circa un'ora prima.",
                "L'opzione 3 arriva 2 h 15 min prima.",
            ),
            (
                "La primera opción llega media hora antes.",
                "La primera opción llega 4 h 00 min antes.",
            ),
            (
                "Die zweite Verbindung kommt eine Stunde vor 12 Uhr an.",
                "Die zweite Verbindung kommt 3 h 30 min vor 12 Uhr an.",
            ),
        ):
            with self.subTest(sentence=sentence):
                final, block = self.final(sentence)
                self.assertTrue(final.endswith(expected), final)
                self.assertEqual(final.count(block), 1)

    def test_a_margin_within_its_words_stays(self) -> None:
        for sentence in (
            "Die erste Verbindung kommt knapp vier Stunden vor 12 Uhr an.",
            "La opción 2 llega unas tres horas y media antes.",
            "Option 3 leaves you about two hours to spare.",
            "L'option 1 te laisse plus de trois heures de marge.",
            "Option 3 arrives 2 h 15 min early.",
            "With option 2 you have a margin of 210 minutes.",
            "L'option 1 te laisse le plus de marge.",
        ):
            with self.subTest(sentence=sentence):
                final, _ = self.final(sentence)
                self.assertTrue(final.endswith(sentence), final)

    def test_a_margin_of_no_option_that_fits_none_goes(self) -> None:
        final, block = self.final("Tu arrives six heures en avance. Bon voyage !")
        self.assertNotIn("six heures", final)
        self.assertTrue(final.endswith("Bon voyage !"), final)
        final, _ = self.final("Tu arrives quatre heures en avance.")
        self.assertIn("quatre heures en avance", final)

    def test_other_durations_and_clock_times_are_not_margins(self) -> None:
        for sentence in (
            "Two Fictive coaches also get you there earlier: walk 5 minutes to the "
            "stop.",
            "Le trajet dure environ deux heures.",
            "Prévois d'être à la gare avant 9 h.",
        ):
            with self.subTest(sentence=sentence):
                final, _ = self.final(sentence)
                self.assertTrue(final.endswith(sentence), final)
        final, _ = self.final("")
        self.assertTrue(final.startswith(OPENING))
