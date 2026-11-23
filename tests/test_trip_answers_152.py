"""Trip plans say what they assume and follow the person (staging -151, -152).

The trip battery of 2026-11-21 through the web UI, read by the offline judge:

- a regional train whose feed gives its number as the headsign was written
  "Train TER direction 3340" (a number is no direction);
- "the best train to Boston before 11:00" led with a coach and gave the train
  second, without saying anything (the mode named comes first);
- "flight at 8am" was planned to one terminal of the airport, "07:00
  requested", though the person gave neither (what is assumed is said);
- "the possible morning times" got one option and no word that it was the
  only one (fewer options than usual are said as such);
- a familiar French conversation read the block's "votre position" and
  "vérifiez-les": a restatement of the block counted as the model's register,
  and the person's "donne moi" was read as no register.

Every stop, street, line and service below is fictional.
"""

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from packages import addressing
from services.orchestrator import transit_planner as tp
from test_register_and_markers import POLITE_LEFT
from test_transit_planner import Base, match

ZONE = "Europe/Paris"
NOW = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)
ORIGIN = "Rue Inventée 3, Villenord"
DESTINATION = "Gare Centrale Fictive"
AIRPORT = "Aéroport Fictif"
LANGUAGES = ("en", "fr", "de", "es", "it")


def _at(clock: str) -> str:
    return f"2026-11-20T{clock}:00Z"


def leg(
    mode: str,
    start: str,
    end: str,
    origin: str,
    target: str,
    name: str = "",
    headsign: str = "",
    **extra: str,
) -> dict[str, Any]:
    return {
        "mode": mode,
        "from": {"name": origin, "tz": ZONE},
        "to": {"name": target, "tz": ZONE},
        "startTime": _at(start),
        "endTime": _at(end),
        "scheduledStartTime": _at(start),
        "scheduledEndTime": _at(end),
        "displayName": name,
        "headsign": headsign,
        **extra,
    }


def trip(*legs: dict[str, Any]) -> dict[str, Any]:
    rides = [x for x in legs if x["mode"] != "WALK"]
    return {
        "startTime": legs[0]["startTime"],
        "endTime": legs[-1]["endTime"],
        "transfers": max(0, len(rides) - 1),
        "legs": list(legs),
    }


def plan(*itineraries: dict[str, Any]) -> dict[str, Any]:
    return {"itineraries": list(itineraries), "direct": []}


def _before(clock: str, minutes: int) -> str:
    hours, rest = divmod(int(clock[:2]) * 60 + int(clock[3:]) - minutes, 60)
    return f"{hours:02d}:{rest:02d}"


def coach(depart: str, arrive: str, name: str) -> dict[str, Any]:
    # The walk ends at the boarding margin before departure.
    walk = (_before(depart, 20), _before(depart, 15))
    return trip(
        leg("WALK", *walk, "START", "Halte Routière Fictive"),
        leg("COACH", depart, arrive, "Halte Routière Fictive", DESTINATION, name),
        leg("WALK", arrive, arrive, DESTINATION, "END"),
    )


def train(depart: str, arrive: str, name: str) -> dict[str, Any]:
    walk = (_before(depart, 20), _before(depart, 15))
    return trip(
        leg("WALK", *walk, "START", "Quai Nord Fictif"),
        leg("LONG_DISTANCE", depart, arrive, "Quai Nord Fictif", DESTINATION, name),
        leg("WALK", arrive, arrive, DESTINATION, "END"),
    )


class Planner:
    """A fake MOTIS 2 server: geocodes and stop searches by text, one plan
    answer, another for a request without coaches."""

    def __init__(
        self,
        first: dict[str, Any],
        narrowed: dict[str, Any] | None = None,
        places: dict[str, list[dict[str, Any]]] | None = None,
        stops: dict[str, list[dict[str, Any]]] | None = None,
    ) -> None:
        self.first, self.narrowed = first, narrowed
        self.places = places or {
            ORIGIN: [
                match("Rue Inventée 3", 48.0, 2.0, ZONE, "Villenord", kind="ADDRESS")
            ],
            DESTINATION: [match(DESTINATION, 51.5, 2.3, ZONE, "Villesud")],
        }
        self.stops = stops or {}
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def __call__(self, path: str, params: dict[str, str]) -> tuple[int, bytes]:
        self.calls.append((path, params))
        if path == "/api/v1/geocode":
            table = self.stops if params.get("type") == "STOP" else self.places
            return 200, json.dumps(table.get(params["text"], [])).encode()
        if path == "/api/v1/reverse-geocode":
            return 200, b"[]"
        if "COACH" not in params["transitModes"] and self.narrowed is not None:
            return 200, json.dumps(self.narrowed).encode()
        return 200, json.dumps(self.first).encode()

    def plans(self) -> list[dict[str, str]]:
        return [p for path, p in self.calls if path.endswith("/plan")]


class Case(Base):
    def planned(
        self, planner: Planner, language: str = "en", **request: Any
    ) -> dict[str, Any]:
        config = self.config()
        arguments = {
            "destination": DESTINATION,
            "origin": ORIGIN,
            "arrive_by": "2026-11-20T12:00",
            **request,
        }
        return asyncio.run(
            tp.compute(
                tp.TransitPlan(**arguments),
                config=config,
                language=language,
                position=None,
                home_zone=ZONE,
                days=[],
                now=NOW,
                client=tp.Client(config, planner),
            )
        )


class HeadsignTest(Case):
    def test_a_number_is_no_direction_and_is_written_once(self) -> None:
        cases = [
            # (line, headsign, codes) -> (line, direction)
            (("TER", "3340", ("3340", "TER")), ("TER 3340", "")),
            (("TER 3340", "3340", ("3340",)), ("TER 3340", "")),
            (("IC", "8 512", ()), ("IC 8 512", "")),
            # The route's own short name, the trip's code before its number,
            # the service name repeated: no direction, nothing added.
            (("Linea Azul", "LA", ("", "LA")), ("Linea Azul", "")),
            (("A", "QIZO", ("QIZO82", "A")), ("A", "")),
            (("Express Fictif", "express fictif", ()), ("Express Fictif", "")),
            # A place stays the direction, digits in its name included.
            (("Bus 2", "Port Fictif", ("2",)), ("Bus 2", "Port Fictif")),
            (("T1", "Terminal 2", ("T1",)), ("T1", "Terminal 2")),
            (("6B", "Villeneuve-sur-Fiction", ()), ("6B", "Villeneuve-sur-Fiction")),
        ]
        for (line, headsign, codes), expected in cases:
            with self.subTest(line=line, headsign=headsign):
                self.assertEqual(tp.directed(line, headsign, codes), expected)

    def test_the_regional_train_reads_as_its_number_in_each_language(self) -> None:
        ride = trip(
            leg("WALK", "07:50", "07:55", "START", "Gare Nord Fictive"),
            leg(
                "REGIONAL_RAIL",
                "08:04",
                "10:04",
                "Gare Nord Fictive",
                DESTINATION,
                "TER",
                "3340",
                tripShortName="3340",
                routeShortName="TER",
            ),
            leg("WALK", "10:04", "10:06", DESTINATION, "END"),
        )
        expected = {
            "en": "Train TER 3340: Gare Nord Fictive 09:04",
            "fr": "Train TER 3340 : Gare Nord Fictive 09:04",
            "de": "Zug TER 3340: Gare Nord Fictive 09:04",
            "es": "Tren TER 3340: Gare Nord Fictive 09:04",
            "it": "Treno TER 3340: Gare Nord Fictive 09:04",
        }
        for language, line in expected.items():
            with self.subTest(language=language):
                data = self.planned(Planner(plan(ride)), language)
                self.assertIn(line, data["rendered"])
                self.assertNotIn("3340 3340", data["rendered"])
                for word in ("direction 3340", "towards 3340", "Richtung 3340"):
                    self.assertNotIn(word, data["rendered"])
                self.assertEqual(data["itineraries"][0]["legs"][1]["headsign"], "")


class RequestedModeTest(Case):
    FIRST = plan(
        coach("05:30", "09:55", "Car Fictif N26"),
        train("05:44", "09:52", "Regional Fictif 66"),
        coach("06:15", "09:40", "Car Fictif 2610"),
    )

    def test_the_mode_named_comes_first(self) -> None:
        planner = Planner(
            self.FIRST, plan(train("06:00", "09:45", "Rapide Fictif 2150"))
        )
        data = self.planned(planner, modes=["train"])
        lines = [i["legs"][1]["line"] for i in data["itineraries"]]
        self.assertEqual(
            lines, ["Regional Fictif 66", "Rapide Fictif 2150", "Car Fictif 2610"]
        )
        rendered = data["rendered"]
        self.assertLess(
            rendered.index("Regional Fictif 66"), rendered.index("Car Fictif 2610")
        )
        # One narrower search, without the coaches that compete with a train.
        plans = planner.plans()
        self.assertEqual(len(plans), 2)
        self.assertIn("COACH", plans[0]["transitModes"])
        modes = plans[1]["transitModes"].split(",")
        self.assertNotIn("COACH", modes)
        self.assertIn("RAIL", modes)
        self.assertIn("BUS", modes)
        self.assertNotIn("No itinerary found by", rendered)

    def test_without_a_mode_the_order_is_the_times(self) -> None:
        planner = Planner(self.FIRST)
        data = self.planned(planner)
        lines = [i["legs"][1]["line"] for i in data["itineraries"]]
        self.assertEqual(
            lines, ["Car Fictif N26", "Regional Fictif 66", "Car Fictif 2610"]
        )
        self.assertEqual(len(planner.plans()), 1)

    def test_no_option_by_the_mode_named_is_said_and_the_others_stay(self) -> None:
        only_coaches = plan(
            coach("05:30", "09:55", "Car Fictif N26"),
            coach("06:15", "09:40", "Car Fictif 2610"),
            coach("06:45", "10:40", "Car Fictif 2612"),
        )
        expected = {
            "en": "No itinerary found by train; the options below use other "
            "means of transport.",
            "fr": "Aucun itinéraire trouvé en train ; les options ci-dessous "
            "utilisent d’autres moyens de transport.",
            "de": "Keine gefundene Verbindung nutzt den Zug; die folgenden "
            "Optionen nutzen andere Verkehrsmittel.",
            "es": "Ningún itinerario encontrado en tren; las opciones siguientes "
            "usan otros medios de transporte.",
            "it": "Nessun itinerario trovato in treno; le opzioni seguenti usano "
            "altri mezzi di trasporto.",
        }
        for language, sentence in expected.items():
            with self.subTest(language=language):
                data = self.planned(
                    Planner(only_coaches, plan()), language, modes=["train"]
                )
                self.assertIn(sentence, data["rendered"])
                self.assertEqual(data["rendered"].count(sentence), 1)
                self.assertEqual(len(data["itineraries"]), 3)

    def test_a_bus_request_takes_a_coach_and_a_local_mode_searches_once(
        self,
    ) -> None:
        data = self.planned(Planner(self.FIRST, plan()), modes=["bus"])
        kinds = [tp.main_kind(i) for i in data["itineraries"]]
        self.assertEqual(kinds, ["coach", "coach", "train"])
        self.assertNotIn("No itinerary found by", data["rendered"])
        # Another language: the first answer is not served from the cache.
        planner = Planner(self.FIRST)
        self.planned(planner, "fr", modes=["ferry"])
        self.assertEqual(len(planner.plans()), 1)
        self.assertEqual(tp.narrowed_modes(("ferry",), False), ())
        self.assertEqual(tp.narrowed_modes(("train", "coach"), False), ())

    def test_a_slower_train_asked_for_is_not_cut_by_a_faster_coach(self) -> None:
        found = plan(
            coach("05:30", "07:30", "Car Fictif N26"),
            train("05:00", "10:30", "Lent Fictif 12"),
        )
        data = self.planned(Planner(found, plan()), modes=["train"])
        self.assertEqual(data["itineraries"][0]["legs"][1]["line"], "Lent Fictif 12")
        data = self.planned(Planner(found, plan()))
        lines = [i["legs"][1]["line"] for i in data["itineraries"]]
        self.assertEqual(lines, ["Car Fictif N26"])

    def test_the_schema_offers_the_modes(self) -> None:
        schema = json.dumps(tp.TransitPlan.model_json_schema())
        for mode in tp.MODES:
            self.assertIn(f'"{mode}"', schema)
        self.assertIn("modes", tp.DESCRIPTION)
        self.assertIn("given_time", tp.DESCRIPTION)


class AssumptionTest(Case):
    ONE = plan(train("05:30", "05:50", "Navette Fictive 7"))

    def test_an_arrival_worked_out_from_a_flight_time_is_said(self) -> None:
        expected = {
            "en": (
                "Arrival by 07:00 assumed, 1 h 00 min before the time given (08:00).",
                "Arrives 06:50 for 07:00 assumed.",
                "requested",
            ),
            "fr": (
                "Arrivée retenue au plus tard à 07:00, 1 h 00 min avant l’heure "
                "indiquée (08:00).",
                "Arrivée 06:50 pour 07:00 retenu.",
                "demandé",
            ),
            "de": (
                "Angenommene Ankunft bis 07:00, 1 h 00 min vor der angegebenen "
                "Zeit (08:00).",
                "Ankunft 06:50, angenommen bis 07:00.",
                "gewünscht",
            ),
            "es": (
                "Llegada supuesta antes de las 07:00, 1 h 00 min antes de la hora "
                "indicada (08:00).",
                "Llegada 06:50 para las 07:00 supuestas.",
                "pedidas",
            ),
            "it": (
                "Arrivo presunto entro le 07:00, 1 h 00 min prima dell’ora "
                "indicata (08:00).",
                "Arrivo 06:50 per le 07:00 presunte.",
                "richieste",
            ),
        }
        for language, (said, check, requested) in expected.items():
            with self.subTest(language=language):
                data = self.planned(
                    Planner(self.ONE),
                    language,
                    arrive_by="2026-11-20T07:00",
                    given_time="08:00",
                )
                rendered = data["rendered"]
                self.assertEqual(rendered.count(said), 1, rendered)
                self.assertIn(check, rendered)
                self.assertNotIn(requested, rendered)
                self.assertIn(check, " ".join(data["passages"]))

    def test_a_deadline_the_person_gave_is_not_an_assumption(self) -> None:
        for given in (None, "07:00", "2026-11-20T07:00"):
            with self.subTest(given=given):
                data = self.planned(
                    Planner(self.ONE), arrive_by="2026-11-20T07:00", given_time=given
                )
                self.assertNotIn("assumed", data["rendered"])
                self.assertIn("Arrives 06:50 for 07:00 requested.", data["rendered"])

    def test_one_of_several_terminals_is_said_with_the_others(self) -> None:
        places = {
            ORIGIN: [
                match("Rue Inventée 3", 48.0, 2.0, ZONE, "Villenord", kind="ADDRESS")
            ],
            AIRPORT: [
                {
                    **match(AIRPORT, 48.70, 2.30, ZONE, "Villesud", kind="PLACE"),
                    "category": "aerodrome",
                }
            ],
        }
        stops = {
            AIRPORT: [
                match(
                    f"{AIRPORT}, Terminal 3 Domestic", 48.705, 2.29, ZONE, "Villesud"
                ),
                match(
                    f"{AIRPORT}, Terminal 1 International",
                    48.71,
                    2.31,
                    ZONE,
                    "Villesud",
                ),
            ]
        }
        data = self.planned(
            Planner(self.ONE, places=places, stops=stops), destination=AIRPORT
        )
        self.assertIn(
            "Destination assumed: Aéroport Fictif, Terminal 3 Domestic (also "
            "found: Aéroport Fictif, Terminal 1 International).",
            data["rendered"],
        )

    def test_a_part_of_the_place_named_is_said_and_the_place_itself_is_not(
        self,
    ) -> None:
        def ending(name: str, asked: str) -> str:
            # Each name asked once: the geocoder's answers are cached.
            places = {
                ORIGIN: [
                    match(
                        "Rue Inventée 3", 48.0, 2.0, ZONE, "Villenord", kind="ADDRESS"
                    )
                ],
                asked: [match(name, 48.2, 2.1, ZONE, "Villesud")],
            }
            data = self.planned(
                Planner(self.ONE, places=places), "fr", destination=asked
            )
            return str(data["rendered"])

        self.assertIn(
            "Destination retenue : Gare Fictive Porte Hachi.",
            ending("Gare Fictive Porte Hachi", "gare fictive"),
        )
        for name, asked in (
            ("Gare Fictive", "Gare Fictive"),
            ("Gare Fictive", "Villesud gare fictive"),
            ("Sud Fictive", "Villesud Sud Fictive station"),
        ):
            with self.subTest(name=name, asked=asked):
                self.assertNotIn("Destination retenue", ending(name, asked))


class FewOptionsTest(Case):
    def test_one_option_is_said_to_be_the_only_one(self) -> None:
        expected = {
            "en": "Only one reasonable itinerary was found that arrives by 12:00.",
            "fr": "Un seul itinéraire raisonnable trouvé arrive au plus tard à 12:00.",
            "de": "Nur eine sinnvolle Verbindung gefunden, die bis 12:00 ankommt.",
            "es": "Solo se encontró un itinerario razonable que llegue antes de "
            "las 12:00.",
            "it": "Solo un itinerario ragionevole trovato arriva entro le 12:00.",
        }
        for language, sentence in expected.items():
            with self.subTest(language=language):
                data = self.planned(
                    Planner(plan(train("08:00", "10:00", "Matin Fictif 1"))),
                    language,
                    arrive_by="2026-11-20T12:00",
                )
                self.assertEqual(data["rendered"].count(sentence), 1)

    def test_two_options_and_no_deadline(self) -> None:
        two = plan(
            train("08:00", "10:00", "Matin Fictif 1"),
            train("08:30", "10:30", "Matin Fictif 3"),
        )
        data = self.planned(Planner(two), arrive_by="2026-11-20T12:00")
        self.assertIn(
            "Only 2 reasonable itineraries were found that arrive by 12:00.",
            data["rendered"],
        )
        data = self.planned(Planner(two), arrive_by=None, depart_at="2026-11-20T08:30")
        self.assertIn(
            "Only 2 reasonable itineraries were found in the time searched.",
            data["rendered"],
        )

    def test_a_full_plan_says_nothing(self) -> None:
        data = self.planned(Planner(RequestedModeTest.FIRST))
        self.assertEqual(len(data["itineraries"]), tp.OPTIONS)
        self.assertNotIn("Only", data["rendered"])


class RegisterTest(Case):
    def block(self, language: str) -> str:
        planner = Planner(plan(train("08:00", "10:00", "Matin Fictif 1")))
        return str(
            self.planned(
                planner,
                language,
                arrive_by="2026-11-20T12:00",
                given_time="12:30",
                modes=["coach"],
            )["rendered"]
        ).replace("Rue Inventée 3 (Villenord)", tp.words(language)["position"])

    def test_a_restated_block_does_not_hide_the_models_register(self) -> None:
        block = self.block("fr")
        self.assertIn("votre position", block)
        own = (
            "Voici les horaires possibles depuis chez toi :\n"
            "Marche 3 min de votre position à Gare Nord Fictive\n"
            "Heures locales, d'après les horaires publiés ; vérifiez-les "
            "auprès de l'exploitant avant de partir."
        )
        styled = tp.in_register(block, "fr", own, "")
        self.assertIn("ta position", styled)
        self.assertIn("vérifie-les", styled)
        self.assertIsNone(POLITE_LEFT["fr"].search(styled))
        # The model's own polite words keep the polite block.
        polite = own.replace("chez toi", "chez vous")
        self.assertEqual(tp.in_register(block, "fr", polite, ""), block)

    def test_the_persons_imperative_says_the_register(self) -> None:
        block = self.block("fr")
        for asked, familiar in (
            ("trouve-moi les horaires de demain matin depuis chez moi", True),
            ("Donne-moi un train pour demain", True),
            ("aide-moi a trouver un train", True),
            ("donnez-moi les horaires depuis chez moi", False),
            ("dites-moi quel train prendre", False),
            ("je dois etre a la gare demain, comment j'y vais depuis chez moi", None),
            ("comme moi, il part demain : quel train ?", None),
        ):
            with self.subTest(asked=asked):
                found = addressing.register(asked, "fr")
                self.assertEqual(
                    found,
                    {True: "informal", False: "formal", None: ""}[familiar],
                )
                styled = tp.in_register(block, "fr", "", asked)
                self.assertEqual("ta position" in styled, familiar is True)

    def test_every_new_trip_sentence_reads_in_both_registers(self) -> None:
        familiar = {
            "fr": "tu peux me dire",
            "de": "kannst du mir sagen",
            "es": "puedes decirme",
            "it": "puoi dirmi",
        }
        keys = (
            "assumed_place",
            "assumed_place_others",
            "assumed_time",
            "assumed_time_from",
            "deadline_assumed",
            "late_assumed",
            "no_mode",
            "few_by",
            "few",
            "board_by",
            "tight",
        )
        for language in LANGUAGES:
            w = tp.words(language)
            for key in keys:
                value = w[key]
                for text in value if isinstance(value, tuple) else (value,):
                    with self.subTest(language=language, key=key):
                        if language in familiar:
                            # No form of address: the same in either register,
                            # and nothing polite left in a familiar turn.
                            self.assertEqual(
                                tp.in_register(text, language, familiar[language]),
                                text,
                            )
                            self.assertIsNone(POLITE_LEFT[language].search(text))
            with self.subTest(language=language, block=True):
                block = self.block(language)
                if language in familiar:
                    styled = tp.in_register(block, language, familiar[language])
                    self.assertIsNone(POLITE_LEFT[language].search(styled), styled)
