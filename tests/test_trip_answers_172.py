"""A trip gets a planner attempt first; the answer says what the planner gave
(staging -172).

Staging -172 (Sydney airport, flight at 8:00 tomorrow, device position
granted): the turn ran three web searches and gave no itinerary, then "no
computed itinerary was found for this trip"; every earlier version planned
it, and the same request was planned in 1.3 s afterwards. The planner's
outcome was logged nowhere, a planner failure was never retried, and the
order of the tools was the model's alone.

- A web search, page read or web journey plan before the planner in a trip
  turn that offers it is answered "call transit_plan first", once.
- A planner that did not answer (timeout, HTTP error) is asked once more;
  every outcome is logged.
- An answer without a computed plan says first whether the planner could
  not be reached or found no itinerary.
- Airport and flight trips are trips in five languages ("at 8", "wie komme
  ich", "cómo llego", "come arrivo").

Every street, stop and service below is fictional.
"""

import asyncio
import json
from typing import Any

from services.orchestrator import journey, travel_claims
from services.orchestrator import transit_planner as tp
from services.orchestrator.chat_pipeline import ChatTools
from services.orchestrator.interactions import Interaction
from services.orchestrator.loop import Call
from test_trip_answers_152 import Case, Planner, plan, train

ONE = plan(train("05:30", "05:50", "Navette Fictive 7"))
ARGUMENTS = {
    "destination": "Gare Centrale Fictive",
    "origin": "Rue Inventée 3, Villenord",
    "arrive_by": "2026-11-20T07:00",
}
PHRASINGS = (
    "how do i get to the airport from here tomorow morning, flight at 8am, public transport pls",
    "flight at 8 tomorrow, best way to the airport by train?",
    "i fly out at 7:45 tmrw, how to reach the airport from here",
    "need to catch a 9am flight tomorrow, what bus to the airport",
    "mon vol part a 8h demain, comment aller a l'aeroport en transports ?",
    "avion demain 7h, je vais comment a l'aeroport depuis chez moi",
    "Mein Flug geht morgen um 8, wie komme ich mit den Öffis zum Flughafen?",
    "mi vuelo sale mañana a las 8, ¿cómo llego al aeropuerto en transporte público?",
    "il mio volo parte domani alle 8, come arrivo all'aeroporto con i mezzi?",
)


class Model:
    def __init__(self, offered: bool) -> None:
        self.tools: list[dict[str, Any]] = (
            [tp.declaration(), journey.declaration()] if offered else []
        )


def tools(offered: bool = True) -> ChatTools:
    runtime = ChatTools(Interaction())
    runtime.model = Model(offered)  # type: ignore[assignment]
    return runtime


def call(name: str, arguments: dict[str, Any] | None = None) -> Call:
    return Call("c1", name, json.dumps(arguments or {"query": "airport train"}))


class PlannerFirstTest(Case):
    def test_airport_trips_are_trips_in_every_language(self) -> None:
        for phrasing in PHRASINGS:
            with self.subTest(phrasing=phrasing):
                self.assertTrue(journey.relevant(phrasing))
        for question in (
            "what is the best airport in Europe?",
            "how big is Sydney airport",
            "list of train stations in Lyon",
        ):
            with self.subTest(question=question):
                self.assertFalse(journey.relevant(question))

    def test_a_search_before_the_planner_is_sent_back_once(self) -> None:
        for first in ("web_search", "web_fetch", journey.NAME):
            with self.subTest(first=first):
                runtime = tools()
                reminder = runtime.planner_first(call(first))
                assert reminder is not None
                self.assertEqual(reminder["error"], "planner_first")
                self.assertIn("call transit_plan first", str(reminder["message"]))
                # Once: a model that searches again is not blocked.
                self.assertIsNone(runtime.planner_first(call(first)))

    def test_after_the_planner_the_web_completes_it(self) -> None:
        runtime = tools()
        self.assertIsNone(runtime.planner_first(call(tp.NAME, ARGUMENTS)))
        self.assertIsNone(runtime.planner_first(call("web_search")))

    def test_a_turn_without_the_planner_searches_freely(self) -> None:
        self.assertIsNone(tools(offered=False).planner_first(call("web_search")))
        self.assertIsNone(tools().planner_first(call("calculator")))


class Flaky(Planner):
    """The planner fails its first plan requests, then answers."""

    def __init__(self, failures: int) -> None:
        super().__init__(ONE)
        self.failures = failures

    async def __call__(self, path: str, params: dict[str, str]) -> tuple[int, bytes]:
        if path.endswith("/plan") and self.failures:
            self.failures -= 1
            self.calls.append((path, params))
            return 503, b"busy"
        return await super().__call__(path, params)


class RetryTest(Case):
    def turn(self) -> travel_claims.Turn:
        return travel_claims.Turn(
            active=True, language="en", zone="Europe/Paris", asked=["by 7am tomorrow"]
        )

    def test_a_planner_that_did_not_answer_is_asked_again(self) -> None:
        planner = Flaky(1)
        turn = self.turn()
        with self.assertLogs(
            "services.orchestrator.transit_planner", "WARNING"
        ) as logs:
            data = self.run_tool(ARGUMENTS, planner, turn)["data"]
        self.assertEqual(data["outcome"], "planned")
        self.assertEqual(len(planner.plans()), 2)
        self.assertEqual(turn.planner_outcome, "")
        logged = [line for line in logs.output if "transit_plan" in line]
        self.assertIn('"attempts": 2', logged[-1])
        self.assertIn('"outcome": "planned"', logged[-1])

    def test_a_planner_down_twice_is_said_unreached(self) -> None:
        planner = Flaky(5)
        turn = self.turn()
        data = self.run_tool(ARGUMENTS, planner, turn)["data"]
        self.assertEqual(data["outcome"], "planner_unavailable")
        self.assertEqual(len(planner.plans()), 2)
        self.assertEqual(turn.planner_outcome, "planner_unavailable")


class PlannerNoteTest(Case):
    def test_an_answer_without_a_plan_says_why_first(self) -> None:
        expected = {
            "en": (
                "The journey planner could not be reached for this trip",
                "The journey planner found no itinerary for this trip",
            ),
            "fr": (
                "Le calculateur d’itinéraires n’a pas pu être joint pour ce trajet",
                "Le calculateur d’itinéraires n’a trouvé aucun itinéraire pour ce trajet",
            ),
            "de": (
                "Der Routenplaner war für diese Reise nicht erreichbar",
                "Der Routenplaner hat für diese Reise keine Verbindung gefunden",
            ),
            "es": (
                "No se pudo consultar el planificador de viajes para este trayecto",
                "El planificador de viajes no encontró ningún itinerario",
            ),
            "it": (
                "Non è stato possibile consultare il pianificatore di viaggio",
                "Il pianificatore di viaggio non ha trovato alcun itinerario",
            ),
        }
        answer = "The airport station is on line T8 (example.org)."
        for language, (unreached, no_route) in expected.items():
            for outcome, sentence in (
                ("planner_unavailable", unreached),
                ("no_itinerary", no_route),
                ("place_not_found", no_route),
            ):
                with self.subTest(language=language, outcome=outcome):
                    turn = travel_claims.Turn(active=True, language=language)
                    turn.planner_outcome = outcome
                    final = tp.planner_note(answer, turn, language)
                    self.assertTrue(final.startswith(sentence), final)
                    self.assertTrue(final.endswith(answer))
                    self.assertEqual(tp.planner_note(final, turn, language), final)

    def test_a_plan_or_a_question_back_says_nothing(self) -> None:
        turn = travel_claims.Turn(active=True, language="en")
        self.assertEqual(tp.planner_note("Answer.", turn, "en"), "Answer.")
        turn.planner_outcome = "ambiguous_place"
        self.assertEqual(tp.planner_note("Which one?", turn, "en"), "Which one?")
        turn.planner_outcome = "no_itinerary"
        turn.plan = "**Public transport plan: A → B**"
        self.assertEqual(tp.planner_note("Answer.", turn, "en"), "Answer.")

    def test_the_planner_outcome_reaches_the_turn(self) -> None:
        turn = travel_claims.Turn(
            active=True, language="en", zone="Europe/Paris", asked=["by 7am"]
        )
        data = self.run_tool(ARGUMENTS, Planner(plan()), turn)["data"]
        self.assertNotIn(data["outcome"], tp.PLANNED_OUTCOMES)
        self.assertEqual(turn.planner_outcome, data["outcome"])
        asyncio.run(asyncio.sleep(0))
