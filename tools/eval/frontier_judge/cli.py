"""Command line: run, calibration, agreement, ledger."""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any

from . import adapters, report
from .adapters import Item
from .client import OpenAIHttp
from .judge import (
    Result,
    Runner,
    Task,
    estimate_usd,
    in_sample,
    load_json,
    missing_evidence,
    pair_body,
    rubrics,
    verdict_body,
)
from .judge import corpus_of
from .ledger import Ledger

DEFAULT_HOME = Path("/opt/atlas-src/eval/frontier-judge")
DEFAULT_TRIP_SCRIPT = Path("/opt/atlas-src/scratchpad/trip_battery_travel2.py")


def collect(args: argparse.Namespace) -> list[Item]:
    cfg = adapters.BatteryConfig(refetch_sources=args.refetch_sources)
    items: list[Item] = []
    for log in args.battery or []:
        items += adapters.battery_items(Path(log), cfg)
    for out in args.trip or []:
        items += adapters.trip_items(
            Path(out), Path(args.trip_script) if args.trip_script else None
        )
    return items


def plan(
    items: list[Item], classes: set[str] | None, model: str, max_output: int
) -> tuple[list[Task], dict[str, Result], dict[str, str]]:
    rubs = rubrics()
    tasks: list[Task] = []
    local: dict[str, Result] = {}
    corpora: dict[str, str] = {}
    for it in items:
        for cls in it.classes:
            if classes and cls not in classes:
                continue
            tid = f"{it.item_id}::{cls}"
            missing = missing_evidence(it, rubs[cls])
            if missing:
                local[tid] = Result(
                    tid,
                    "",
                    "",
                    "local",
                    "unknown",
                    reason="missing evidence: "
                    + "; ".join("/".join(g) for g in missing),
                )
                continue
            tasks.append(
                Task(
                    tid,
                    it.item_id,
                    cls,
                    verdict_body(it, rubs[cls], model, max_output),
                    source=it.source,
                )
            )
            corpora[tid] = corpus_of(it)
    pair_cfg = load_json("rubrics.json")["pairwise"]
    groups: dict[str, list[Item]] = {}
    for it in items:
        if it.group:
            groups.setdefault(it.group, []).append(it)
    for members in groups.values():
        members.sort(key=lambda i: i.source)
        for a, b in zip(members, members[1:]):
            for cls in pair_cfg["classes"]:
                if classes and cls not in classes:
                    continue
                if missing_evidence(a, rubs[cls]) or missing_evidence(b, rubs[cls]):
                    continue
                pid = f"pair::{cls}::{a.item_id}|{b.item_id}"
                for suffix, x, y in (("ab", a, b), ("ba", b, a)):
                    tid = f"{pid}::{suffix}"
                    tasks.append(
                        Task(
                            tid,
                            a.item_id,
                            cls,
                            pair_body(
                                x, y, rubs[cls], pair_cfg["question"], model, max_output
                            ),
                            (x.item_id, y.item_id),
                            source=a.source,
                        )
                    )
                    corpora[tid] = corpus_of(a, b)
    return tasks, local, corpora


def arbiter_tasks(
    tasks: list[Task],
    bulk: dict[str, Result],
    items: dict[str, Item],
    models: dict[str, Any],
    arbiter: str,
) -> list[Task]:
    percent = int(models["arbiter_sample_percent"])
    low = float(models["low_confidence"])
    prioritised: list[tuple[int, Task]] = []
    for task in tasks:
        res = bulk.get(task.task_id)
        if res is None:
            continue
        reason = 9
        if task.pair:
            other = task.task_id[:-2] + ("ba" if task.task_id.endswith("ab") else "ab")
            o = bulk.get(other)
            if o and {"A": "B", "B": "A"}.get(o.verdict, o.verdict) != res.verdict:
                reason = 1
        else:
            if report.code_disagreement(
                res.verdict, items[task.item_id].code_checks.get(task.cls, [])
            ):
                reason = 0
            elif res.unverified_quotes or res.error:
                reason = 2
            elif res.confidence < low:
                reason = 3
        if reason == 9 and in_sample(task.task_id, percent):
            reason = 5
        if reason < 9:
            t = copy.deepcopy(task)
            t.body["model"] = arbiter
            prioritised.append((reason, t))
    prioritised.sort(key=lambda p: p[0])
    return [t for _, t in prioritised]


def fit_budget(
    tasks: list[Task],
    ledger: Ledger,
    models: dict[str, Any],
    mode: str,
    margin: float = 0.95,
) -> tuple[list[Task], int]:
    month, run = ledger.totals()
    room = (
        min(ledger.run_cap_eur - run, ledger.month_cap_eur - month)
        * margin
        / ledger.usd_to_eur
    )
    kept: list[Task] = []
    total = 0.0
    for t in tasks:
        cost = estimate_usd(t.body, models, mode)
        if total + cost > room:
            break
        kept.append(t)
        total += cost
    return kept, len(tasks) - len(kept)


def cmd_run(args: argparse.Namespace) -> int:
    models = load_json("models.json")
    bulk_model = args.bulk_model or models["roles"]["bulk"]
    arbiter_model = args.arbiter_model or models["roles"]["arbiter"]
    run_id = args.run_id or dt.datetime.now(dt.UTC).strftime("judge-%Y%m%dT%H%M%SZ")
    out = Path(args.out_dir or DEFAULT_HOME / "runs") / run_id
    out.mkdir(parents=True, exist_ok=True)
    items = collect(args)
    if args.only:
        items = [it for it in items if re.search(args.only, it.item_id)]
    by_id = {it.item_id: it for it in items}
    classes = set(args.classes.split(",")) if args.classes else None
    tasks, local, corpora = plan(
        items, classes, bulk_model, int(models["max_output_tokens"])
    )
    estimate = sum(estimate_usd(t.body, models, args.mode) for t in tasks)
    print(
        f"{len(items)} items, {len(tasks)} judge requests, {len(local)} unknown for missing evidence; "
        f"upper-bound estimate {estimate:.2f} USD on {bulk_model} ({args.mode})"
    )
    if args.dry_run:
        report.dump(
            out / "plan.json",
            {
                "tasks": [t.task_id for t in tasks],
                "local": {k: v.reason for k, v in local.items()},
            },
        )
        return 0
    caps = {
        "month": float(args.month_cap or models["caps_eur"]["month"]),
        "run": float(args.run_cap or models["caps_eur"]["run"]),
    }
    ledger = Ledger(
        Path(args.ledger or DEFAULT_HOME / "ledger.jsonl"),
        run_id,
        caps["month"],
        caps["run"],
        float(models["usd_to_eur"]),
    )
    runner = Runner(OpenAIHttp(), ledger, models, out, poll_seconds=args.poll_seconds)
    results, notes = runner.run(tasks, args.mode, corpora, args.batch_timeout)
    bulk = {r.task_id: r for r in results}
    arb: dict[str, Result] = {}
    if not args.no_arbiter and bulk:
        candidates = arbiter_tasks(tasks, bulk, by_id, models, arbiter_model)
        kept, dropped = fit_budget(candidates, ledger, models, args.mode)
        if dropped:
            notes.append(
                f"arbiter: {dropped} of {len(candidates)} re-judgements dropped to stay within the caps"
            )
        arb_results, arb_notes = runner.run(
            kept, args.mode, corpora, args.batch_timeout
        )
        notes += arb_notes
        arb = {r.task_id: r for r in arb_results}
    cadence = {
        "battery": report.cadence_per_day(Path(args.battery_dir), "*.log"),
        "trip": report.cadence_per_day(Path(args.trip_dir), "trip_*.out"),
    }
    text, data = report.build_report(
        run_id,
        by_id,
        {t.task_id: t for t in tasks},
        local,
        bulk,
        arb,
        notes,
        ledger.totals(),
        caps,
        float(models["usd_to_eur"]),
        cadence,
    )
    (out / "report.md").write_text(text, encoding="utf-8")
    report.dump(out / "results.json", data)
    print(f"report: {out / 'report.md'}")
    print(
        f"cost this run: {data['cost_eur']:.4f} EUR; month to date {data['month_to_date_eur']:.4f} EUR; projected month {data['projected_month_eur']:.2f} EUR"
    )
    return 0


def cmd_calibration(args: argparse.Namespace) -> int:
    items = collect(args)
    path = Path(
        args.output
        or DEFAULT_HOME / "calibration" / f"review-{dt.date.today().isoformat()}.md"
    )
    counts = report.write_review(
        items, rubrics(), args.classes.split(","), args.per_class, path
    )
    print(f"review file: {path}")
    print(json.dumps(counts))
    return 0


def cmd_agreement(args: argparse.Namespace) -> int:
    labels = report.read_labels(Path(args.review))
    results: dict[str, Any] = {}
    for path in args.results:
        results.update(json.loads(Path(path).read_text(encoding="utf-8"))["results"])
    print(json.dumps(report.agreement(labels, results), indent=1))
    return 0


def cmd_ledger(args: argparse.Namespace) -> int:
    models = load_json("models.json")
    ledger = Ledger(
        Path(args.ledger or DEFAULT_HOME / "ledger.jsonl"),
        "-",
        float(models["caps_eur"]["month"]),
        float(models["caps_eur"]["run"]),
        float(models["usd_to_eur"]),
    )
    month, _ = ledger.totals()
    print(f"month to date: {month:.4f} EUR of {ledger.month_cap_eur:.2f} EUR")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="frontier_judge", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    def inputs(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--battery",
            action="append",
            help="staging battery .log (its .d/ folder is read too)",
        )
        p.add_argument(
            "--trip", action="append", help="trip battery output (trip_*.out)"
        )
        p.add_argument(
            "--trip-script",
            default=str(DEFAULT_TRIP_SCRIPT),
            help="trip battery script, read for its CASES",
        )
        p.add_argument(
            "--refetch-sources",
            action="store_true",
            help="fetch cited public URLs again (battery logs URLs only)",
        )

    run = sub.add_parser("run", help="judge battery evidence")
    inputs(run)
    run.add_argument(
        "--classes",
        help="comma-separated class ids (default: every class with evidence)",
    )
    run.add_argument(
        "--only",
        help="regular expression on item ids (for example a re-run of a few items)",
    )
    run.add_argument("--mode", choices=["batch", "standard"], default="batch")
    run.add_argument("--bulk-model")
    run.add_argument("--arbiter-model")
    run.add_argument("--no-arbiter", action="store_true")
    run.add_argument("--run-id")
    run.add_argument("--out-dir")
    run.add_argument("--ledger")
    run.add_argument("--run-cap", type=float)
    run.add_argument("--month-cap", type=float)
    run.add_argument("--batch-timeout", type=float, default=4 * 3600)
    run.add_argument("--poll-seconds", type=float, default=30.0)
    run.add_argument("--battery-dir", default="/opt/atlas-src/logs/staging-battery")
    run.add_argument("--trip-dir", default="/opt/atlas-src/scratchpad")
    run.add_argument("--dry-run", action="store_true")
    run.set_defaults(func=cmd_run)

    cal = sub.add_parser(
        "calibration", help="write the owner's calibration review file"
    )
    inputs(cal)
    cal.add_argument("--classes", default="C1,C2,C3,C10")
    cal.add_argument("--per-class", type=int, default=30)
    cal.add_argument("--output")
    cal.set_defaults(func=cmd_calibration)

    agr = sub.add_parser(
        "agreement", help="agreement between owner labels and a run's verdicts"
    )
    agr.add_argument("--review", required=True)
    agr.add_argument(
        "--results",
        required=True,
        action="append",
        help="results.json of a run (repeatable)",
    )
    agr.set_defaults(func=cmd_agreement)

    led = sub.add_parser("ledger", help="month-to-date spend")
    led.add_argument("--ledger")
    led.set_defaults(func=cmd_ledger)

    args = parser.parse_args(argv)
    code: int = args.func(args)
    return code


if __name__ == "__main__":
    sys.exit(main())
