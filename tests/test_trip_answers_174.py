"""A planner timeout gets its retry; a time the call writes otherwise is read
(staging -174).

Staging -174 (Tokyo to Kyoto, "tomorrow before 10 am"): the planner did not
answer within 8 s, the 15 s of the tool call left 6.8 s, and the retry
waited for a full 8 s that was not there; the model's next call was then
rejected as invalid_time. The first attempt now keeps room for a second, a
retry needs only a short time, and a time with seconds, an offset, "10am" or
"10 h" is read. Every street, stop and service below is fictional.
"""

import asyncio
import json
from typing import Any
from unittest.mock import patch
from zoneinfo import ZoneInfo

from services.orchestrator import transit_planner as tp
from services.orchestrator import travel_claims
from test_transit_planner import NOW
from test_trip_answers_152 import Case, Planner, plan, train

ONE = plan(train("05:30", "05:50", "Navette Fictive 7"))
ARGUMENTS = {
    "destination": "Gare Centrale Fictive",
    "origin": "Rue Inventée 3, Villenord",
    "arrive_by": "2026-11-20T07:00",
}
TOKYO = ZoneInfo("Asia/Tokyo")


class Slow(Planner):
    """The planner's first plan request hangs longer than its share."""

    def __init__(self, seconds: float) -> None:
        super().__init__(ONE)
        self.seconds = seconds

    async def __call__(self, path: str, params: dict[str, str]) -> tuple[int, bytes]:
        if path.endswith("/plan") and self.seconds:
            self.calls.append((path, params))
            seconds, self.seconds = self.seconds, 0.0
            await asyncio.sleep(seconds)
        return await super().__call__(path, params)


class RetryTimeTest(Case):
    def execute(self, planner: Planner, timeout: float) -> dict[str, Any]:
        token = travel_claims.current.set(
            travel_claims.Turn(
                active=True, language="en", zone="Europe/Paris", asked=["by 7am"]
            )
        )
        try:
            with (
                patch.object(tp.Client, "_fetch", planner),
                patch.object(tp, "_now", lambda: NOW),
            ):
                result = asyncio.run(tp.execute(json.dumps(ARGUMENTS), timeout))
        finally:
            travel_claims.current.reset(token)
        data = result["data"]
        assert isinstance(data, dict)
        return data

    def test_a_hung_first_attempt_leaves_time_for_the_retry(self) -> None:
        # The -174 budget scaled down: the first attempt may use 60 % of it
        # (4.8 s of 8), the retry the 3 s left.
        planner = Slow(6.0)
        with self.assertLogs(
            "services.orchestrator.transit_planner", "WARNING"
        ) as logs:
            data = self.execute(planner, 8.0)
        self.assertEqual(data["outcome"], "planned")
        self.assertEqual(len(planner.plans()), 2)
        self.assertIn(
            '"attempts": 2', [x for x in logs.output if "transit_plan" in x][-1]
        )

    def test_no_retry_without_time_for_it(self) -> None:
        planner = Slow(4.0)
        data = self.execute(planner, 2.2)
        self.assertEqual(data["outcome"], "planner_unavailable")
        self.assertEqual(len(planner.plans()), 1)


class CallTimeTest(Case):
    def test_times_written_otherwise_are_read(self) -> None:
        now = NOW
        days = ["2026-11-24"]
        for written, expected in (
            ("2026-11-24T10:00", "2026-11-24 10:00"),
            ("2026-11-24T10:00:00", "2026-11-24 10:00"),
            ("2026-11-24T10:00:00+09:00", "2026-11-24 10:00"),
            ("2026-11-24T01:00:00Z", "2026-11-24 10:00"),
            ("2026-11-24 10:00", "2026-11-24 10:00"),
            ("10:00", "2026-11-24 10:00"),
            ("10am", "2026-11-24 10:00"),
            ("10 AM", "2026-11-24 10:00"),
            ("9:45 p.m.", "2026-11-24 21:45"),
            ("10h", "2026-11-24 10:00"),
            ("10 h 30", "2026-11-24 10:30"),
            ("10.00", "2026-11-24 10:00"),
            ("10", "2026-11-24 10:00"),
        ):
            with self.subTest(written=written):
                found = tp.local_time(written, TOKYO, now, days)
                assert found is not None
                self.assertEqual(found.strftime("%Y-%m-%d %H:%M"), expected)
                self.assertEqual(found.tzinfo, TOKYO)

    def test_what_is_no_time_stays_invalid_and_is_logged(self) -> None:
        for written in ("tomorrow morning", "25:00", "13pm", "2026-11-24"):
            with self.subTest(written=written):
                with self.assertRaises(tp.TransitError) as raised:
                    tp.local_time(written, TOKYO, NOW, [])
                self.assertEqual(raised.exception.code, "invalid_time")
        turn = travel_claims.Turn(
            active=True, language="en", zone="Europe/Paris", asked=["by 7am"]
        )
        with self.assertLogs(
            "services.orchestrator.transit_planner", "WARNING"
        ) as logs:
            data = self.run_tool(
                {**ARGUMENTS, "arrive_by": "tomorrow morning"}, Planner(ONE), turn
            )["data"]
        self.assertEqual(data["outcome"], "invalid_time")
        self.assertIn("tomorrow morning", logs.output[-1])

    def test_the_call_of_minus_174_plans(self) -> None:
        turn = travel_claims.Turn(
            active=True, language="en", zone="Europe/Paris", asked=["by 7am"]
        )
        data = self.run_tool(
            {**ARGUMENTS, "arrive_by": "2026-11-20T07:00:00+01:00"}, Planner(ONE), turn
        )["data"]
        self.assertEqual(data["outcome"], "planned")
