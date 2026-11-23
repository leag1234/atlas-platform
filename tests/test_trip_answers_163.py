"""An arrival time is "requested" only when the person gave it (staging -163).

Staging -163 (Sydney airport, "flight at 8:00 AM tomorrow"): the model planned
an arrival by 06:30 and gave no given_time, so every option read "Arrives
06:09 for 06:30 requested" and no sentence said that 06:30 was worked out.
Code now reads the person's own messages for times of day in any common form;
a deadline they did not give is "assumed" on every option, and the plan says
so once, with the time they gave when there is one. Every street, stop and
service below is fictional.
"""

import asyncio
from datetime import datetime, timezone
from typing import Any

from services.orchestrator import transit_planner as tp
from test_trip_answers_152 import DESTINATION, ORIGIN, Case, Planner, plan, train

# One train arriving 06:50 local (05:50 UTC).
ONE = plan(train("05:30", "05:50", "Navette Fictive 7"))


class RequestedTest(Case):
    def with_words(
        self,
        asked: list[str],
        language: str = "en",
        arrive_by: str = "2026-11-20T07:00",
        **request: Any,
    ) -> str:
        config = self.config()
        data = asyncio.run(
            tp.compute(
                tp.TransitPlan(
                    destination=DESTINATION,
                    origin=ORIGIN,
                    arrive_by=arrive_by,
                    **request,
                ),
                config=config,
                language=language,
                position=None,
                home_zone="Europe/Paris",
                days=[],
                now=datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc),
                client=tp.Client(config, Planner(ONE)),
                asked=asked,
            )
        )
        return str(data["rendered"])

    def test_a_deadline_worked_out_from_a_flight_names_both_times(self) -> None:
        cases = {
            "en": (
                "how do i get to the airport tomorrow, flight at 8:00 AM",
                "Arrival by 07:00 assumed, 1 h 00 min before the time given (08:00).",
                "Arrives 06:50 for 07:00 assumed.",
                "requested",
            ),
            "fr": (
                "comment j'y vais demain, mon vol est a 8h",
                "Arrivée retenue au plus tard à 07:00, 1 h 00 min avant l’heure "
                "indiquée (08:00).",
                "Arrivée 06:50 pour 07:00 retenu.",
                "demandé",
            ),
            "de": (
                "wie komme ich morgen hin, Flug um 8 Uhr",
                "Angenommene Ankunft bis 07:00, 1 h 00 min vor der angegebenen Zeit "
                "(08:00).",
                "Ankunft 06:50, angenommen bis 07:00.",
                "gewünscht",
            ),
            "es": (
                "cómo llego mañana, mi vuelo sale a las 8:00",
                "Llegada supuesta antes de las 07:00, 1 h 00 min antes de la hora "
                "indicada (08:00).",
                "Llegada 06:50 para las 07:00 supuestas.",
                "pedidas",
            ),
            "it": (
                "come ci arrivo domani, il volo parte alle 8.00",
                "Arrivo presunto entro le 07:00, 1 h 00 min prima dell’ora indicata "
                "(08:00).",
                "Arrivo 06:50 per le 07:00 presunte.",
                "richieste",
            ),
        }
        for language, (asked, said, line, requested) in cases.items():
            with self.subTest(language=language):
                rendered = self.with_words([asked], language)
                self.assertEqual(rendered.count(said), 1, rendered)
                self.assertIn(line, rendered)
                self.assertNotIn(requested, rendered)

    def test_a_deadline_with_no_time_of_the_person_is_assumed(self) -> None:
        cases = {
            "en": "Arrival by 07:00 assumed: no arrival time was given.",
            "fr": "Arrivée retenue au plus tard à 07:00 : aucune heure d’arrivée "
            "n’a été indiquée.",
            "de": "Angenommene Ankunft bis 07:00: Es wurde keine Ankunftszeit "
            "angegeben.",
            "es": "Llegada supuesta antes de las 07:00: no se indicó ninguna hora "
            "de llegada.",
            "it": "Arrivo presunto entro le 07:00: non è stata indicata alcuna ora "
            "di arrivo.",
        }
        for language, said in cases.items():
            with self.subTest(language=language):
                rendered = self.with_words(["early tomorrow morning please"], language)
                self.assertEqual(rendered.count(said), 1, rendered)

    def test_a_deadline_the_person_gave_is_requested_in_any_form(self) -> None:
        for asked, arrive_by in (
            ("I need to be there tomorrow by 7am", "2026-11-20T07:00"),
            ("je dois y etre demain a 7h", "2026-11-20T07:00"),
            ("j'ai rendez-vous a 7 h 00 demain", "2026-11-20T07:00"),
            ("un dejeuner a midi trente demain", "2026-11-20T12:30"),
            ("ich muss morgen um 7 Uhr dort sein", "2026-11-20T07:00"),
            ("tengo que estar a las 07:00", "2026-11-20T07:00"),
            ("devo essere li alle 7.00", "2026-11-20T07:00"),
            ("be there at 2:30 pm", "2026-11-20T14:30"),
        ):
            with self.subTest(asked=asked):
                rendered = self.with_words([asked], "en", arrive_by)
                self.assertIn("requested.", rendered)
                self.assertNotIn("assumed", rendered)

    def test_the_continuation_instruction_is_not_the_persons_time(self) -> None:
        # The person's messages only: the latest that says a time counts too.
        rendered = self.with_words(
            ["flight at 8:00 tomorrow", "and from the station instead?"]
        )
        self.assertIn("1 h 00 min before the time given (08:00)", rendered)

    def test_without_the_persons_words_the_deadline_stays_requested(self) -> None:
        rendered = self.with_words([])
        self.assertIn("Arrives 06:50 for 07:00 requested.", rendered)
        self.assertNotIn("assumed", rendered)

    def test_the_person_clocks(self) -> None:
        self.assertEqual(
            tp.person_clocks(["flight at 8:00 AM", "lunch at midi trente", "um 8.15"]),
            {480, 750, 495},
        )
