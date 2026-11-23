"""Latency and accuracy of the request router on synthetic cases.

Every case is synthetic (tests/fixtures/router_cases.json), never a real
conversation. The routing calls go to a running model gateway (real providers,
small cost, printed at the end):

    ATLAS_GATEWAY_URL=http://127.0.0.1:8210 python tools/router_bench.py \
        --repeat 2 --concurrency 4

--single PROFILE measures one call alone, without hedging, on the gateway text
role --role (router, router_alt, or profile: the profile's own model).
--in-process calls the gateway code of this checkout in this process, with the
provider keys of the environment, instead of a running gateway: a gateway that
has not been deployed yet. --local measures the local classifier alone (no
call, no cost). --legacy replays the schedule before the router role (the
fast profile, then the retry profile at half an 8 s bound, no local
classifier) through the same gateway, for a before/after in the same window.
The router's own environment (ATLAS_MODEL_ROUTER_TIMEOUT,
ATLAS_MODEL_ROUTER_HEDGE_MS, ATLAS_HIDDEN_PROFILES) applies otherwise. The report prints latency percentiles, the decided share,
the kind accuracy (after the code rules of request_router.settle) and the
follow-up accuracy, and never a key.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import Counter
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.orchestrator import request_router  # noqa: E402

CASES = Path(__file__).resolve().parents[1] / "tests/fixtures/router_cases.json"
_APP = "/api/atlas/artifacts/" + "0" * 31 + "1/versions/1/open"

HISTORIES: dict[str, list[dict[str, str]]] = {
    "app": [
        {"role": "user", "content": "make a small snake game"},
        {
            "role": "assistant",
            "content": f"Here is your snake game: [Open the app]({_APP}). "
            "Use the arrow keys to move.",
        },
    ],
    "drawing": [
        {"role": "user", "content": "draw a house on a hill"},
        {
            "role": "assistant",
            "content": f"Here is the drawing: [Open]({_APP}). A house, a hill and a sun.",
        },
    ],
    "file": [
        {"role": "user", "content": "an excel file to track my expenses"},
        {
            "role": "assistant",
            "content": "Your file is ready: [expenses.xlsx](/deliverables/expenses.xlsx). "
            "Columns: date, label, amount, total.",
        },
    ],
    "file_pptx": [
        {"role": "user", "content": "a 5 slide deck about bees"},
        {
            "role": "assistant",
            "content": "Your deck is ready: [bees.pptx](/deliverables/bees.pptx).",
        },
    ],
    "run": [
        {"role": "user", "content": "simulate 10 coin flips in python and run it"},
        {
            "role": "assistant",
            "content": "I ran the script.\n```python\nimport random\n"
            "print([random.choice('HT') for _ in range(10)])\n```\n"
            "Output: ['H', 'T', 'T', 'H', 'H', 'T', 'H', 'H', 'T', 'T']",
        },
    ],
    "script_shown": [
        {
            "role": "user",
            "content": "write a python script that renames my photos by date",
        },
        {
            "role": "assistant",
            "content": "Here is the script:\n```python\nimport os\nfor name in "
            "os.listdir('.'):\n    print(name)\n```",
        },
    ],
    "remote": [
        {"role": "user", "content": "check the uptime of my vps"},
        {
            "role": "assistant",
            "content": "On your VPS: up 12 days, load 0.3. The nginx service is "
            "inactive. Do you want me to restart it?",
        },
    ],
    "location_asked": [
        {"role": "user", "content": "il y a une boulangerie ouverte près de moi ?"},
        {
            "role": "assistant",
            "content": "Where are you right now? Tell me a street or a landmark, "
            "or let me use your device location.",
        },
    ],
}


def load(path: Path = CASES) -> list[dict[str, object]]:
    return json.loads(path.read_text())["cases"]


def percentile(values: list[float], share: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    return ordered[min(len(ordered) - 1, int(share * len(ordered)))]


async def in_process(payload: dict[str, object], seconds: float) -> dict[str, object]:
    from agent_provider import AgentProvider

    return await AgentProvider().complete(payload)


async def run(
    cases: list[dict[str, object]],
    repeat: int,
    concurrency: int,
    single: str | None,
    *,
    role: str = "router",
    legacy: bool = False,
    complete: request_router.Complete | None = None,
    local: bool = False,
) -> dict[str, object]:
    limit = asyncio.Semaphore(concurrency)
    rows: list[dict[str, object]] = []

    async def one(case: dict[str, object]) -> None:
        history = HISTORIES.get(str(case.get("history") or ""), [])
        text = str(case["text"])
        async with limit:
            started = time.monotonic()
            if local:
                routed = request_router.local_decision(text, history)
                milliseconds = (time.monotonic() - started) * 1000
                spent, reason, attempts = Decimal(0), "", 0
            elif single:
                bound = request_router.timeout()
                routed, spent, reason = await request_router._attempt(
                    text, history, single, bound, complete, role
                )
                milliseconds = (time.monotonic() - started) * 1000
                attempts = 1
            else:
                (
                    routed,
                    milliseconds,
                    spent,
                    reason,
                    attempts,
                ) = await request_router._classify(text, history, complete)
        model_decided = routed is not None
        if routed is None and not legacy and not single:
            # As request_router.route: the local classifier decides.
            routed = request_router.local_decision(text, history)
        elif routed is None and legacy:
            # Before: a request naming a file format still went to the file path;
            # any other turn went to the word patterns (counted as wrong here).
            routed = request_router.deliver_fallback(text)
        # The code rules apply to every decision, as in request_router.route.
        routed = request_router.settle(text, routed)
        accepted = case.get("accept") or [case["kind"]]
        rows.append(
            {
                "ms": milliseconds,
                "cost": spent,
                "reason": reason,
                "attempts": attempts,
                "decided": model_decided,
                "kind_ok": routed is not None and routed.kind in accepted,
                "follow_ok": routed is not None
                and routed.follow_up == bool(case.get("follow_up", False)),
                "expected": case["kind"],
                "got": routed.kind if routed else "none",
            }
        )

    await asyncio.gather(*(one(case) for _ in range(repeat) for case in cases))
    milliseconds = [float(row["ms"]) for row in rows]
    total = len(rows)
    confusion = Counter(
        (row["expected"], row["got"]) for row in rows if not row["kind_ok"]
    )
    return {
        "turns": total,
        "p50_ms": round(percentile(milliseconds, 0.5)),
        "p90_ms": round(percentile(milliseconds, 0.9)),
        "p99_ms": round(percentile(milliseconds, 0.99)),
        "max_ms": round(max(milliseconds)),
        "no_model_decision": sum(not row["decided"] for row in rows),
        "second_attempts": sum(int(row["attempts"]) > 1 for row in rows),
        "kind_accuracy": round(sum(bool(row["kind_ok"]) for row in rows) / total, 3),
        "follow_up_accuracy": round(
            sum(bool(row["follow_ok"]) for row in rows) / total, 3
        ),
        "failures": dict(Counter(str(row["reason"]) for row in rows if row["reason"])),
        "errors": {f"{a}->{b}": n for (a, b), n in confusion.most_common(12)},
        "cost_eur": str(sum((Decimal(row["cost"]) for row in rows), Decimal(0))),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--single", help="one profile alone, no hedging")
    parser.add_argument(
        "--role",
        default="router",
        help="gateway text role of --single: router, router_alt, profile",
    )
    parser.add_argument("--in-process", action="store_true")
    parser.add_argument("--local", action="store_true")
    parser.add_argument("--legacy", action="store_true")
    parser.add_argument("--cases", type=Path, default=CASES)
    arguments = parser.parse_args()
    if arguments.local:
        request_router.local_decision("warm up", [])
    if arguments.legacy:
        first, *rest = request_router.profiles()
        request_router.attempts = lambda: (
            (first, "profile", False),
            (rest[0], "profile", False),
        )
        request_router.timeout = lambda: 8.0
        request_router.hedge = lambda: 4.0
    report = asyncio.run(
        run(
            load(arguments.cases),
            arguments.repeat,
            arguments.concurrency,
            arguments.single,
            role=arguments.role,
            legacy=arguments.legacy,
            complete=in_process if arguments.in_process else None,
            local=arguments.local,
        )
    )
    print(json.dumps(report, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
