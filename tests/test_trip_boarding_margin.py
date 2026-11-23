"""Boarding margins before long-distance trains and coaches (staging -151).

Staging -151 (2026-11-21, trip battery): for "Madrid (Barrio de los Austrias)
to Barcelona-Sants, arrive by 13:00 tomorrow" every option walked from the
Cercanias platform at Atocha onto an AVE 0 to 5 min before it left, although
the AVE access control closes 2 min before departure after a luggage scan;
the New York to Boston plan walked onto an Amtrak train 1 min before it left.
A plan now keeps BOARDING_MARGIN_MINUTES at the stop of every long-distance
departure (MOTIS 2 modes HIGHSPEED_RAIL, LONG_DISTANCE, NIGHT_RAIL, COACH):
the first boarding starts the trip earlier and says by when to be at the stop;
a tight change is asked again with that margin added to every change, and is
written only when nothing else is found, said as tight.

Every stop, line and street below is fictional.
"""

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from services.orchestrator import transit_planner as tp
from test_transit_planner import Base, match

ZONE = "Europe/Madrid"
NOW = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)
ORIGIN = "Calle Inventada 3, Villanorte"
DESTINATION = "Estacion Sur Ficticia"
PLACES = {
    ORIGIN: [
        match("Calle Inventada 3", 40.0, -3.0, ZONE, "Villanorte", kind="ADDRESS")
    ],
    DESTINATION: [match("Estacion Sur Ficticia", 41.6, -0.4, ZONE, "Villasur")],
    "Central Ficticia": [match("Central Ficticia", 40.01, -3.01, ZONE, "Villanorte")],
}


def _at(clock: str) -> str:
    return f"2026-11-20T{clock}:00Z"


def leg(
    mode: str, start: str, end: str, origin: str, target: str, name: str = ""
) -> dict[str, Any]:
    seconds = int(
        (
            datetime.fromisoformat(_at(end).replace("Z", "+00:00"))
            - datetime.fromisoformat(_at(start).replace("Z", "+00:00"))
        ).total_seconds()
    )
    return {
        "mode": mode,
        "from": {"name": origin, "tz": ZONE},
        "to": {"name": target, "tz": ZONE},
        "startTime": _at(start),
        "endTime": _at(end),
        "scheduledStartTime": _at(start),
        "scheduledEndTime": _at(end),
        "duration": seconds,
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


def plan(*itineraries: dict[str, Any]) -> dict[str, Any]:
    return {"itineraries": list(itineraries), "direct": []}


# A commuter train onto a high-speed train with no margin after the walk (the
# Madrid defect, in UTC: 08:20 local is 07:20).
TIGHT = trip(
    leg("WALK", "07:14", "07:20", "START", "Plaza Ficticia"),
    leg(
        "SUBURBAN",
        "07:20",
        "07:25",
        "Plaza Ficticia",
        "Central Ficticia Cercanias",
        "C9",
    ),
    leg("WALK", "07:25", "07:27", "Central Ficticia Cercanias", "Central Ficticia"),
    leg(
        "HIGHSPEED_RAIL",
        "07:27",
        "10:00",
        "Central Ficticia",
        "Estacion Sur Ficticia",
        "AVE 01234",
    ),
    leg("WALK", "10:00", "10:05", "Estacion Sur Ficticia", "END"),
)
# The same request with 15 min more per change, as MOTIS 2 answers it: the
# walk lasts 2 + 15 min, an earlier commuter train reaches the same AVE.
SAFE = trip(
    leg("WALK", "06:54", "07:00", "START", "Plaza Ficticia"),
    leg(
        "SUBURBAN",
        "07:00",
        "07:05",
        "Plaza Ficticia",
        "Central Ficticia Cercanias",
        "C9",
    ),
    leg("WALK", "07:05", "07:22", "Central Ficticia Cercanias", "Central Ficticia"),
    leg(
        "HIGHSPEED_RAIL",
        "07:27",
        "10:00",
        "Central Ficticia",
        "Estacion Sur Ficticia",
        "AVE 01234",
    ),
    leg("WALK", "10:00", "10:05", "Estacion Sur Ficticia", "END"),
)
# A walk to a coach terminal, then a long-distance coach.
COACH = trip(
    leg("WALK", "07:24", "07:30", "START", "Terminal Ficticia"),
    leg(
        "COACH",
        "07:30",
        "10:30",
        "Terminal Ficticia",
        "Estacion Sur Ficticia",
        "Express 77",
    ),
    leg("WALK", "10:30", "10:32", "Estacion Sur Ficticia", "END"),
)
# A local trip: bus then tram with a 1 min change.
LOCAL = trip(
    leg("WALK", "07:15", "07:20", "START", "Parada Uno"),
    leg("BUS", "07:20", "07:30", "Parada Uno", "Parada Dos", "B4"),
    leg("WALK", "07:30", "07:31", "Parada Dos", "Parada Dos Tranvia"),
    leg("TRAM", "07:31", "07:40", "Parada Dos Tranvia", "Estacion Sur Ficticia", "T2"),
    leg("WALK", "07:40", "07:42", "Estacion Sur Ficticia", "END"),
)


class Planner:
    """A fake MOTIS 2 server: one answer for the plan, another for the same
    request with ``additionalTransferTime``."""

    def __init__(self, first: dict[str, Any], margin: dict[str, Any] | None) -> None:
        self.first = first
        self.margin = margin
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def __call__(self, path: str, params: dict[str, str]) -> tuple[int, bytes]:
        self.calls.append((path, params))
        if path == "/api/v1/geocode":
            return 200, json.dumps(PLACES.get(params["text"], [])).encode()
        if path == "/api/v1/reverse-geocode":
            return 200, b"[]"
        if "additionalTransferTime" in params and self.margin is not None:
            return 200, json.dumps(self.margin).encode()
        return 200, json.dumps(self.first).encode()

    def plans(self) -> list[dict[str, str]]:
        return [p for path, p in self.calls if path.endswith("/plan")]


class Margin(Base):
    def plan(
        self,
        planner: Planner,
        *,
        language: str = "en",
        origin: str = ORIGIN,
        arrive_by: str | None = "2026-11-20T13:00",
        depart_at: str | None = None,
    ) -> dict[str, Any]:
        config = self.config()
        return asyncio.run(
            tp.compute(
                tp.TransitPlan(
                    destination=DESTINATION,
                    origin=origin,
                    arrive_by=arrive_by,
                    depart_at=depart_at,
                ),
                config=config,
                language=language,
                position=None,
                home_zone=ZONE,
                days=[],
                now=NOW,
                client=tp.Client(config, planner),
            )
        )


class HighSpeedChangeTest(Margin):
    def test_a_tight_change_gives_way_to_an_earlier_connection(self) -> None:
        planner = Planner(plan(TIGHT), plan(SAFE))
        data = self.plan(planner)
        self.assertEqual(data["outcome"], "planned")
        plans = planner.plans()
        self.assertEqual(len(plans), 2)
        self.assertNotIn("additionalTransferTime", plans[0])
        self.assertEqual(
            plans[1]["additionalTransferTime"], str(tp.BOARDING_MARGIN_MINUTES)
        )
        rendered = data["rendered"]
        # The same AVE, reached by the earlier commuter train; the walk is
        # written with its own length, not the margin the request added.
        self.assertIn(
            "Train C9: Plaza Ficticia 08:00 → Central Ficticia Cercanias 08:05",
            rendered,
        )
        self.assertIn(
            "Walk 2 min from Central Ficticia Cercanias to Central Ficticia", rendered
        )
        self.assertIn("Train AVE 01234: Central Ficticia 08:27", rendered)
        self.assertNotIn("08:20", rendered)
        self.assertNotIn("Tight connection", rendered)
        self.assertEqual(len(data["itineraries"]), 1)
        self.assertNotIn("tight", data["itineraries"][0])
        self.assertEqual(data["itineraries"][0]["depart"], "2026-11-20T07:54+01:00")

    def test_only_a_tight_change_is_written_and_said_as_tight(self) -> None:
        expected = {
            "en": "Tight connection: 0 min between the arrival at Central Ficticia "
            "and the departure of AVE 01234, which closes boarding before it "
            "leaves; no option with more time was found.",
            "fr": "Correspondance serrée : 0 min entre l’arrivée à "
            "Central Ficticia et le départ de AVE 01234",
            "es": "Conexión justa: 0 min entre la llegada a Central Ficticia y "
            "la salida de AVE 01234, que cierra el embarque antes de salir",
            "de": "Knapper Anschluss: 0 min zwischen der Ankunft in Central "
            "Ficticia und der Abfahrt von AVE 01234",
            "it": "Coincidenza stretta: 0 min tra l’arrivo a Central Ficticia "
            "e la partenza di AVE 01234",
        }
        for language, sentence in expected.items():
            with self.subTest(language=language):
                planner = Planner(plan(TIGHT), plan())
                data = self.plan(planner, language=language)
                self.assertEqual(data["outcome"], "planned")
                self.assertIn(sentence, data["rendered"])
                self.assertIn(sentence, " ".join(data["passages"]))
                self.assertEqual(
                    data["itineraries"][0]["tight"],
                    [
                        {
                            "line": "AVE 01234",
                            "kind": "train",
                            "stop": "Central Ficticia",
                            "minutes": 0,
                        }
                    ],
                )

    def test_a_server_that_ignores_the_margin_changes_no_walk(self) -> None:
        # The second answer is the first one again: its 2 min walk is no
        # 2 + 15 min walk, so nothing is taken off it and the change stays tight.
        planner = Planner(plan(TIGHT), plan(TIGHT))
        data = self.plan(planner)
        rendered = data["rendered"]
        self.assertEqual(len(data["itineraries"]), 1)
        self.assertIn("Walk 2 min from Central Ficticia Cercanias", rendered)
        self.assertIn("Tight connection: 0 min", rendered)

    def test_an_earlier_option_with_margin_wins_over_a_later_tight_one(self) -> None:
        # The deadline leaves slack: the option with margin is the answer even
        # though the tight one leaves later (the latest departures are kept).
        later = json.loads(
            json.dumps(TIGHT).replace("T07:", "T08:").replace("T10:", "T11:")
        )
        # The commuter train of 08:00 reaches the 08:27 AVE 20 min early.
        early = json.loads(json.dumps(SAFE))
        walk = early["legs"][2]
        walk["endTime"] = walk["scheduledEndTime"] = _at("07:07")
        walk["duration"] = 120
        planner = Planner(plan(early, later), plan())
        data = self.plan(planner)
        departures = [i["depart"][11:16] for i in data["itineraries"]]
        self.assertEqual(departures, ["07:54"])
        self.assertNotIn("Tight connection", data["rendered"])

    def test_every_long_distance_mode_and_no_local_one(self) -> None:
        for mode, tight in (
            ("HIGHSPEED_RAIL", True),
            ("LONG_DISTANCE", True),
            ("NIGHT_RAIL", True),
            ("COACH", True),
            ("REGIONAL_RAIL", False),
            ("SUBURBAN", False),
            ("BUS", False),
            ("SUBWAY", False),
            ("TRAM", False),
        ):
            with self.subTest(mode=mode):
                body = json.loads(json.dumps(plan(TIGHT)))
                body["itineraries"][0]["legs"][3]["mode"] = mode
                found = tp.parse(body, tp.zone_of(ZONE))
                self.assertEqual(bool(found[0].get("tight")), tight)

    def test_the_margin_is_the_named_constant_at_its_bound(self) -> None:
        # 15 min after the walk is enough, 14 is not.
        for minutes, tight in ((15, False), (14, True)):
            with self.subTest(minutes=minutes):
                body = json.loads(json.dumps(plan(TIGHT)))
                walk = body["itineraries"][0]["legs"][2]
                end = f"07:{25 + 2:02d}"
                walk["endTime"] = walk["scheduledEndTime"] = _at(end)
                ride = body["itineraries"][0]["legs"][3]
                depart = f"07:{27 + minutes:02d}"
                ride["startTime"] = ride["scheduledStartTime"] = _at(depart)
                found = tp.parse(body, tp.zone_of(ZONE))
                self.assertEqual(bool(found[0].get("tight")), tight)


class FirstBoardingTest(Margin):
    def test_a_walk_to_a_coach_starts_early_enough(self) -> None:
        planner = Planner(plan(COACH), None)
        data = self.plan(planner)
        self.assertEqual(len(planner.plans()), 1)
        rendered = data["rendered"]
        self.assertIn("leave 08:09, arrive 11:32, 3 h 23 min, direct", rendered)
        self.assertIn(
            "- Be at Terminal Ficticia by 08:15, 15 min before departure: "
            "long-distance trains and coaches close boarding before they leave.\n"
            "- Coach Express 77: Terminal Ficticia 08:30",
            rendered,
        )
        itinerary = data["itineraries"][0]
        self.assertEqual(itinerary["depart"], "2026-11-20T08:09+01:00")
        self.assertEqual(itinerary["legs"][0]["arrive"], "2026-11-20T08:15+01:00")
        self.assertEqual(itinerary["legs"][1]["board_by"], "2026-11-20T08:15+01:00")
        self.assertEqual(itinerary["legs"][1]["depart"], "2026-11-20T08:30+01:00")
        self.assertNotIn("tight", itinerary)
        # Every clock of the block is a time of the plan for the checks.
        self.assertTrue(
            {"08:09", "08:15", "08:30"} <= tp._plan_times(data["itineraries"])
        )

    def test_the_boarding_time_in_each_language(self) -> None:
        expected = {
            "fr": (
                "Être à Terminal Ficticia au plus tard à 08:15, 15 min avant le départ",
                "tu peux partir",
            ),
            "es": (
                "Estar en Terminal Ficticia a las 08:15 como tarde, "
                "15 min antes de la salida",
                "puedes salir",
            ),
            "de": (
                "Spätestens um 08:15 in Terminal Ficticia sein, 15 min vor der Abfahrt",
                "du kannst fahren",
            ),
            "it": (
                "Essere a Terminal Ficticia entro le 08:15, "
                "15 min prima della partenza",
                "puoi partire",
            ),
        }
        for language, (sentence, informal) in expected.items():
            with self.subTest(language=language):
                data = self.plan(Planner(plan(COACH), None), language=language)
                self.assertIn(sentence, data["rendered"])
                # An infinitive, no form of address: the same in the informal
                # register of the conversation.
                self.assertIn(
                    sentence, tp.in_register(data["rendered"], language, informal)
                )

    def test_a_trip_from_the_station_itself_says_when_to_be_there(self) -> None:
        station = trip(
            leg(
                "LONG_DISTANCE",
                "07:30",
                "10:30",
                "Central Ficticia",
                "Estacion Sur Ficticia",
                "IC 501",
            ),
            leg("WALK", "10:30", "10:32", "Estacion Sur Ficticia", "END"),
        )
        data = self.plan(Planner(plan(station), None), origin="Central Ficticia")
        rendered = data["rendered"]
        self.assertIn("leave 08:15, arrive 11:32", rendered)
        self.assertIn(
            "- Be at Central Ficticia by 08:15, 15 min before departure", rendered
        )
        self.assertEqual(data["itineraries"][0]["depart"], "2026-11-20T08:15+01:00")

    def test_a_departure_too_soon_after_now_is_left_out(self) -> None:
        # Asked at 08:20 for "now": the coach of 08:30 leaves 10 min after the
        # walk could start, less than the margin; no other option is found.
        config = self.config()
        late = datetime(2026, 11, 20, 7, 20, tzinfo=timezone.utc)
        planner = Planner(plan(COACH), None)
        found = asyncio.run(
            tp._planned(
                tp.Client(config, planner),
                plan(COACH),
                (ORIGIN, DESTINATION),
                late,
                zone=tp.zone_of(ZONE),
                arrive_by=False,
                language="en",
                flights=False,
            )
        )
        kept, verdict = tp.selected(found, now=late, deadline=None)
        self.assertEqual((kept, verdict), ([], "no_itinerary"))


class LocalTripTest(Margin):
    def test_a_local_trip_is_unchanged(self) -> None:
        planner = Planner(plan(LOCAL), None)
        data = self.plan(planner, arrive_by="2026-11-20T09:00")
        self.assertEqual(len(planner.plans()), 1)
        self.assertNotIn("additionalTransferTime", planner.plans()[0])
        rendered = data["rendered"]
        self.assertIn("leave 08:15, arrive 08:42, 27 min, 1 change", rendered)
        self.assertIn("Walk 1 min from Parada Dos to Parada Dos Tranvia", rendered)
        self.assertIn("Tram T2: Parada Dos Tranvia 08:31", rendered)
        for word in ("Be at", "Tight connection", "board_by"):
            self.assertNotIn(word, rendered)
        itinerary = data["itineraries"][0]
        self.assertNotIn("tight", itinerary)
        self.assertFalse(any("board_by" in x for x in itinerary["legs"]))
