"""A trip goes exactly to the precision the person gave; a house number is
placed; a short follow-up keeps the conversation's language.

- A place named as an area (country, region, city, district) is reached when
  a ride arrives inside it and left when a ride leaves it, read from the
  router's administrative areas of the stops; a station, an airport, an
  address or a place of interest is planned to as named.
- A house number the planner's geocoder places only on its street is placed
  by the map service Atlas uses for places, so the first walk and stops are
  measured from the house.
- A short follow-up keeps the conversation's language, with every note.
- A sentence saying whether a line stops somewhere is not known is no route
  claim.

Every place, stop, line and street below is fictional.
"""

import asyncio
import json
from dataclasses import dataclass
from typing import Any

from packages.language import conversation_language
from services.orchestrator import transit_planner as tp
from services.orchestrator import travel_claims
from test_transit_planner import Base, NOW

ZONE = "Europe/Brussels"


def area(name: str, level: int, default: bool = False) -> dict[str, Any]:
    """An administrative area as the router's geocoder writes it."""
    return {"name": name, "adminLevel": float(level), "default": default}


COUNTRY = [area("Fictivia", 2), area("Region Fictive", 4, True)]
# A city's stops: the city is their default area.

NORD = [area("Fictivia", 2), area("Region Fictive", 4), area("Villenord", 8, True)]
SUD = [area("Fictivia", 2), area("Region Fictive", 4), area("Villesud", 8, True)]
CENTRE = [*SUD[:2], area("Villesud", 8), area("Villesud Centre", 9, True)]


def place(
    name: str, lat: float, lon: float, areas: list[dict[str, Any]], **extra: Any
) -> dict[str, Any]:
    return {
        "type": "PLACE",
        "category": "place_6",
        "name": name,
        "lat": lat,
        "lon": lon,
        "tz": ZONE,
        "score": -20.0,
        "areas": areas,
        **extra,
    }


def stop(
    name: str, lat: float, lon: float, areas: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "type": "STOP",
        "name": name,
        "lat": lat,
        "lon": lon,
        "tz": ZONE,
        "score": -20.0,
        "areas": areas,
        "modes": ["RAIL", "BUS"],
    }


# Stops: two in Villenord, three in Villesud, one in the countryside between.
STOPS = {
    "Arret du Parc": (48.00, 2.00, NORD),
    "Gare de Villenord": (48.01, 2.01, NORD),
    "Halte des Champs": (48.50, 2.10, COUNTRY),
    "Gare de Villesud": (49.00, 2.30, SUD),
    "Place Ronde": (49.01, 2.31, CENTRE),
    "Musee Fictif": (49.02, 2.32, CENTRE),
}


def _at(clock: str) -> str:
    return f"2026-11-20T{clock}:00Z"


def leg(
    mode: str, start: str, end: str, origin: str, target: str, name: str = ""
) -> dict[str, Any]:
    def where(stop_name: str) -> dict[str, Any]:
        if stop_name in STOPS:
            lat, lon, _ = STOPS[stop_name]
            return {"name": stop_name, "tz": ZONE, "lat": lat, "lon": lon}
        return {"name": stop_name, "tz": ZONE}

    return {
        "mode": mode,
        "from": where(origin),
        "to": where(target),
        "startTime": _at(start),
        "endTime": _at(end),
        "scheduledStartTime": _at(start),
        "scheduledEndTime": _at(end),
        "displayName": name,
        "headsign": "",
    }


def trip(*legs: dict[str, Any]) -> dict[str, Any]:
    rides = [x for x in legs if x["mode"] != "WALK"]
    return {
        "startTime": legs[0]["startTime"],
        "endTime": legs[-1]["endTime"],
        "transfers": max(0, len(rides) - 1),
        "legs": list(legs),
    }


# Door to door: a bus in Villenord, a train to Villesud, a tram there.
FULL = trip(
    leg("WALK", "06:50", "06:55", "START", "Arret du Parc"),
    leg("BUS", "06:55", "07:05", "Arret du Parc", "Gare de Villenord", "B3"),
    leg("WALK", "07:05", "07:10", "Gare de Villenord", "Gare de Villenord"),
    leg(
        "REGIONAL_RAIL",
        "07:15",
        "08:15",
        "Gare de Villenord",
        "Gare de Villesud",
        "R12",
    ),
    leg("WALK", "08:15", "08:18", "Gare de Villesud", "Gare de Villesud"),
    leg("TRAM", "08:20", "08:28", "Gare de Villesud", "Place Ronde", "T4"),
    leg("WALK", "08:28", "08:35", "Place Ronde", "END"),
)
# Within Villesud only.
LOCAL = trip(
    leg("WALK", "07:00", "07:05", "START", "Gare de Villesud"),
    leg("TRAM", "07:05", "07:15", "Gare de Villesud", "Place Ronde", "T4"),
    leg("WALK", "07:15", "07:20", "Place Ronde", "END"),
)


class Router:
    """A fake MOTIS 2 server: geocodes by text, stops near a point with their
    areas, one plan answer."""

    def __init__(
        self, places: dict[str, list[dict[str, Any]]], plan: dict[str, Any]
    ) -> None:
        self.places, self.plan = places, plan
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def __call__(self, path: str, params: dict[str, str]) -> tuple[int, bytes]:
        self.calls.append((path, params))
        if path == "/api/v1/geocode":
            return 200, json.dumps(self.places.get(params["text"], [])).encode()
        if path == "/api/v1/reverse-geocode":
            lat, lon = (float(x) for x in params["place"].split(","))
            near = [
                stop(n, a, b, areas)
                for n, (a, b, areas) in STOPS.items()
                if abs(a - lat) < 0.002 and abs(b - lon) < 0.002
            ]
            return 200, json.dumps(near).encode()
        return 200, json.dumps(self.plan).encode()

    def plans(self) -> list[dict[str, str]]:
        return [p for path, p in self.calls if path.endswith("/plan")]


PLACES = {
    "Villenord": [place("Villenord", 48.005, 2.005, NORD)],
    "Villesud": [place("Villesud", 49.015, 2.315, SUD)],
    "Villesud Centre": [
        place("Villesud Centre", 49.015, 2.315, CENTRE, category="suburb")
    ],
    "Region Fictive": [place("Region Fictive", 48.5, 2.1, COUNTRY, category="state")],
    "Gare de Villesud": [stop("Gare de Villesud", 49.00, 2.30, SUD)],
    "Musee Fictif": [
        {**place("Musee Fictif", 49.02, 2.32, CENTRE), "category": "museum"}
    ],
    "3 rue Inventee, Villenord": [
        {
            "type": "ADDRESS",
            "name": "Rue Inventee",
            "lat": 48.0,
            "lon": 2.0,
            "tz": ZONE,
            "score": -10.0,
            "areas": NORD,
        }
    ],
}


class PrecisionTest(Base):
    houses: Any = None

    def plan(
        self,
        origin: str,
        destination: str,
        body: dict[str, Any],
        language: str = "en",
        depart: str = "07:30",
    ) -> dict[str, Any]:
        config = self.config()
        router = Router(PLACES, {"itineraries": [body], "direct": []})
        self.router = router
        return asyncio.run(
            tp.compute(
                tp.TransitPlan(
                    destination=destination,
                    origin=origin,
                    depart_at=f"2026-11-20T{depart}",
                ),
                config=config,
                language=language,
                position=None,
                home_zone=ZONE,
                days=[],
                now=NOW,
                client=tp.Client(config, router, houses=self.houses),
            )
        )

    def lines(self, data: dict[str, Any]) -> list[str]:
        return [leg["line"] or leg["kind"] for leg in data["itineraries"][0]["legs"]]

    def test_a_city_is_reached_when_a_ride_arrives_in_it(self) -> None:
        data = self.plan("Villenord", "Villesud", FULL)
        legs = data["itineraries"][0]["legs"]
        self.assertEqual(
            [leg["line"] for leg in legs if leg["kind"] != "walk"], ["R12"]
        )
        self.assertEqual(legs[-1]["to"], "Gare de Villesud")
        self.assertEqual(data["itineraries"][0]["arrive"][11:16], "09:15")
        rendered = data["rendered"]
        self.assertNotIn("T4", rendered)
        self.assertNotIn("B3", rendered)
        self.assertEqual(rendered.count("The plan ends on arrival in Villesud"), 1)

    def test_every_granularity(self) -> None:
        for destination, rides, last in (
            # A city: the train is the way in.
            ("Villesud", ["B3", "R12"], "Gare de Villesud"),
            # A district: the tram enters it; the walk beyond goes.
            ("Villesud Centre", ["B3", "R12", "T4"], "Place Ronde"),
            # A region the whole trip is in: nothing to cut.
            ("Region Fictive", ["B3", "R12", "T4"], "END"),
            # A stop, a place of interest: planned to as named.
            ("Gare de Villesud", ["B3", "R12", "T4"], "END"),
            ("Musee Fictif", ["B3", "R12", "T4"], "END"),
        ):
            with self.subTest(destination=destination):
                data = self.plan("3 rue Inventee, Villenord", destination, FULL)
                legs = data["itineraries"][0]["legs"]
                self.assertEqual(
                    [leg["line"] for leg in legs if leg["kind"] != "walk"], rides
                )
                self.assertEqual(legs[-1]["to"], last)

    def test_a_city_origin_starts_where_the_trip_leaves_it(self) -> None:
        data = self.plan("Villenord", "Musee Fictif", FULL, "fr")
        legs = data["itineraries"][0]["legs"]
        self.assertEqual(legs[0]["line"], "R12")
        self.assertIn(
            "Le plan commence là où le trajet quitte Villenord", data["rendered"]
        )

    def test_a_trip_inside_the_city_is_not_cut(self) -> None:
        data = self.plan("Gare de Villesud", "Villesud", LOCAL)
        self.assertEqual(
            [
                leg["line"]
                for leg in data["itineraries"][0]["legs"]
                if leg["kind"] != "walk"
            ],
            ["T4"],
        )
        self.assertNotIn("ends on arrival", data["rendered"])

    def test_the_sentence_in_five_languages(self) -> None:
        for language, start in (
            ("en", "The plan ends on arrival in Villesud"),
            ("fr", "Le plan s’arrête à l’arrivée à Villesud"),
            ("de", "Der Plan endet bei der Ankunft in Villesud"),
            ("es", "El plan termina a la llegada a Villesud"),
            ("it", "Il piano termina all’arrivo a Villesud"),
        ):
            with self.subTest(language=language):
                self.assertIn(
                    start,
                    self.plan("Villenord", "Villesud", FULL, language)["rendered"],
                )

    def test_the_area_of_a_match(self) -> None:
        self.assertEqual(
            tp.area_key(PLACES["Villesud"][0]), (frozenset({"villesud"}), 8)
        )
        # The geocoder names the area in the request's language, the stops in
        # the local one: both names are the area.
        named = {**PLACES["Villesud"][0], "name": "Villesud-sur-Fiction"}
        self.assertEqual(
            tp.area_key(named), (frozenset({"villesud", "villesud sur fiction"}), 8)
        )
        self.assertIsNone(tp.area_key(PLACES["Gare de Villesud"][0]))
        self.assertIsNone(tp.area_key(PLACES["3 rue Inventee, Villenord"][0]))
        self.assertIsNone(tp.area_key(PLACES["Musee Fictif"][0]))


class HouseNumberTest(PrecisionTest):
    """Inherits the precision cases' helpers; adds the house number ones."""

    def test_a_house_number_is_placed_and_planned_from(self) -> None:
        async def houses(written: str) -> Any:
            return [
                {
                    "lat": "48.02",
                    "lon": "2.02",
                    "address": {"house_number": "3", "road": "Rue Inventee"},
                }
            ]

        self.houses = houses
        data = self.plan("3 rue Inventee, Villenord", "Gare de Villesud", FULL)
        self.assertEqual(self.router.plans()[0]["fromPlace"], "48.02000,2.02000")
        self.assertIn("3 Rue Inventee (Villenord)", data["rendered"])

    def test_another_house_or_a_far_one_is_not_taken(self) -> None:
        for number, (answer, depart) in enumerate(
            (
                (
                    [
                        {
                            "lat": "48.02",
                            "lon": "2.02",
                            "address": {"house_number": "30"},
                        }
                    ],
                    "07:31",
                ),
                (
                    [{"lat": "45.0", "lon": "5.0", "address": {"house_number": "3"}}],
                    "07:32",
                ),
                ([], "07:33"),
            )
        ):
            with self.subTest(number=number):

                async def houses(written: str, answer: Any = answer) -> Any:
                    return answer

                self.houses = houses
                self.plan(
                    "3 rue Inventee, Villenord", "Gare de Villesud", FULL, depart=depart
                )
                self.assertEqual(
                    self.router.plans()[0]["fromPlace"], "48.00000,2.00000"
                )

    def test_the_same_street_in_two_towns_is_the_one_in_the_town_named(self) -> None:
        def address(town: str, lat: float) -> dict[str, Any]:
            return {
                "type": "ADDRESS",
                "name": "Rue des Tilleuls 8",
                "lat": lat,
                "lon": 2.0,
                "score": -30.0,
                "areas": [area("Province Fictive", 6), area(town, 8, True)],
            }

        matches = [address("Bourg-Amont", 47.0), address("Villenord", 48.0)]
        chosen = tp.choose(matches, biased=False, named="8 rue des Tilleuls, Villenord")
        self.assertEqual(chosen["lat"], 48.0)
        with self.assertRaises(tp.TransitError) as raised:
            tp.choose(matches, biased=False, named="8 rue des Tilleuls")
        self.assertEqual(raised.exception.code, "ambiguous_place")
        # A province both towns lie in tells nothing.
        with self.assertRaises(tp.TransitError):
            tp.choose(
                matches, biased=False, named="8 rue des Tilleuls, Province Fictive"
            )

    def test_a_client_with_its_own_fetch_asks_no_map_service(self) -> None:
        self.assertIsNone(tp.Client(self.config(), Router({}, {})).houses)
        self.assertIsNotNone(tp.Client(self.config()).houses)


class BareHourTest(Base):
    def test_a_bare_hour_after_a_time_word_is_the_persons_time(self) -> None:
        self.assertEqual(
            tp.person_clocks(
                ["flight at 11 tomorrow", "avion à 7 demain", "um 8 dort"]
            ),
            {660, 420, 480},
        )
        self.assertEqual(tp.person_clocks(["at 3 km from here", "in 5 min"]), set())


@dataclass
class Message:
    role: str
    text: str


class LanguageTest(Base):
    def test_a_short_follow_up_keeps_the_conversations_language(self) -> None:
        history = [
            Message(
                "user", "je cherche un train pour Villesud samedi, départ vers 9 h"
            ),
            Message(
                "assistant",
                "Voici les trains de samedi pour Villesud.",
            ),
        ]
        for follow_up in (
            "et la ligne 7 elle passe par la halte du parc ?",
            "y a-t-il un bus de nuit ce soir ?",
            "est-ce que la ligne 21 va a la gare",
        ):
            with self.subTest(follow_up=follow_up):
                for locale in ("en", "fr"):
                    self.assertEqual(
                        conversation_language(
                            [*history, Message("user", follow_up)], locale
                        ),
                        "fr",
                    )
        for follow_up, language in (
            ("¿Me lo puedes explicar en español, por favor?", "es"),
            ("Can you answer in English please, I don't speak French", "en"),
            ("Kannst du mir das bitte auf Deutsch sagen?", "de"),
        ):
            with self.subTest(follow_up=follow_up):
                self.assertEqual(
                    conversation_language([*history, Message("user", follow_up)], "fr"),
                    language,
                )


class DisclaimerTest(Base):
    def test_a_sentence_saying_a_line_is_not_verified_claims_nothing(self) -> None:
        for language, claim, disclaimer in (
            (
                "es",
                "Toma la línea T7 desde Plaza Fictiva hasta Estación Norte.",
                "No hay información verificada sobre si la línea T7 va desde Plaza "
                "Fictiva hasta Estación Norte.",
            ),
            (
                "en",
                "Take line T7 from Plaza Fictiva to North Station.",
                "It could not be confirmed whether line T7 runs from Plaza Fictiva "
                "to North Station.",
            ),
        ):
            with self.subTest(language=language):
                turn = travel_claims.Turn(
                    active=True, language=language, zone=ZONE, asked=["T7 ?"]
                )
                self.assertTrue(turn.findings(claim).route.spans)
                self.assertEqual(turn.findings(disclaimer).route.spans, [])
