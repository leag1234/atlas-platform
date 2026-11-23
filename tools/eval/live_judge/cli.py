"""Command line: serve, backfill, report, summary, estimate."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import signal
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tools.eval.frontier_judge.client import OpenAIHttp

from . import engine, evidence, guard, judge, server, source, store


def when(value: str) -> float:
    """A UTC date (2026-11-23), an ISO time, or a relative age (90m, 6h, 2d)."""
    value = value.strip()
    units = {"m": 60, "h": 3600, "d": 86400}
    if value[:-1].isdigit() and value[-1:] in units:
        return time.time() - int(value[:-1]) * units[value[-1]]
    stamp = dt.datetime.fromisoformat(value)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=dt.UTC)
    return stamp.timestamp()


def _http(cfg: engine.Config, key_file: Path) -> OpenAIHttp:
    key = engine.load_key(key_file)
    return OpenAIHttp(api_key=key, timeout=120.0, retries=2, backoff=3.0)


def cmd_serve(args: argparse.Namespace) -> int:
    cfg = engine.Config.from_env()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    scores = store.Store(cfg.store_path)
    eng = engine.Engine(cfg, _http(cfg, args.key_file), scores)
    if not scores.get_meta("judge_epoch"):
        scores.set_meta("judge_epoch", str(time.time()))
    httpd = server.serve(
        scores,
        cfg.webui_db,
        cfg.chat_ui_url,
        cfg.port,
        lambda: float(scores.get_meta("judge_epoch") or time.time()),
    )
    stop = threading.Event()

    def halt(*_: object) -> None:
        stop.set()
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, halt)
    signal.signal(signal.SIGINT, halt)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    logging.info(
        "live judge on %s (port %d, daily cap %.2f EUR)",
        cfg.identity["ATLAS_PUBLIC_URL"],
        cfg.port,
        cfg.daily_cap_eur,
    )
    eng.loop(stop)
    return 0


def _range_answers(
    cfg: engine.Config, since: float, until: float
) -> list[source.Answer]:
    db = source.connect_ro(cfg.webui_db)
    try:
        answers = source.finished_since(db, since, until, limit=100_000)
    finally:
        db.close()
    return [a for a in answers if since <= a.created_at < until]


def cmd_backfill(args: argparse.Namespace) -> int:
    cfg = engine.Config.from_env()
    scores = store.Store(cfg.store_path)
    since, until = when(args.since), when(args.until) if args.until else time.time()
    todo = []
    for answer in _range_answers(cfg, since, until):
        known = scores.get(answer.message_id)
        if known is None or known["state"] in ("pending", "budget"):
            todo.append(answer)
    print(
        f"{len(todo)} answers to judge between {args.since} and {args.until or 'now'}"
    )
    if args.dry_run:
        return 0
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    eng = engine.Engine(
        cfg,
        _http(cfg, args.key_file),
        scores,
        run_id=lambda: f"live-judge-backfill-{stamp}",
        run_cap_eur=args.cap,
    )
    todo.sort(key=eng.automated)
    done = budget = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for record in pool.map(eng.judge_one, todo):
            done += 1
            budget += record["state"] == "budget"
            if done % 25 == 0:
                print(f"{done}/{len(todo)} judged")
    month, run = eng.ledger().totals()
    print(
        f"judged {done}, budget refusals {budget}, backfill spend {run:.3f} EUR, month {month:.2f} EUR"
    )
    store.write_summaries(scores, cfg.summary_dir)
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    cfg = engine.Config.from_env()
    scores = store.Store(cfg.store_path)
    rows = scores.rows(when(args.since), version=args.version)
    scored = [r for r in rows if r["state"] == "scored" and r["score"] is not None]
    if args.cls:
        scored = [r for r in scored if args.cls in json.loads(r["classes"] or "[]")]
    if args.json:
        print(json.dumps(store.summarize(rows), indent=1))
        return 0
    summary = store.summarize(rows)
    print(
        f"{summary['answers']} answers, {summary['scored']} scored, mean {summary['mean']}, "
        f"median {summary['median']}, below 5: {summary['share_below_5']}, cost {summary['cost_eur']} EUR"
    )
    print("states:", summary["states"])
    print(
        "distribution:",
        " ".join(f"{k}:{v}" for k, v in summary["distribution"].items()),
    )
    print("by class:", summary["by_class"])
    print()
    worst = sorted(scored, key=lambda r: (r["score"], -r["answered_at"]))[: args.worst]
    for r in worst:
        stamp = dt.datetime.fromtimestamp(r["answered_at"], dt.UTC).strftime(
            "%m-%d %H:%M"
        )
        classes = ",".join(json.loads(r["classes"] or "[]")) or "-"
        print(
            f"{r['score']:>2} {stamp} {r['version'][:24]:<24} {r['task_type'] or '-':<9} {classes:<14} user {r['user_hash']}"
        )
        print(f"   {r['verdict']}")
        if r["worst"]:
            print(f"   worst: {r['worst']}")
        print(
            f"   {cfg.identity['ATLAS_PUBLIC_URL']}/c/{r['chat_id']}  message {r['message_id']}"
        )
    return 0


def cmd_summary(args: argparse.Namespace) -> int:
    cfg = engine.Config.from_env()
    scores = store.Store(cfg.store_path)
    day = dt.date.fromisoformat(args.day) if args.day else None
    for path in store.write_summaries(scores, cfg.summary_dir, day):
        print(path)
    return 0


def cmd_estimate(args: argparse.Namespace) -> int:
    """Upper-bound cost per answer (no request sent) and the daily cost at the observed volume."""
    cfg = engine.Config.from_env()
    since = when(args.since)
    answers = _range_answers(cfg, since, time.time())
    table = judge.models()
    sample = answers[-args.sample :]
    estimates = []
    db = source.connect_ro(cfg.webui_db)
    try:
        for answer in sample:
            ev = evidence.build(answer, db, cfg.paths)
            estimates.append(
                judge.estimate_usd(
                    judge.body(ev, cfg.bulk_model, answer.updated_at), table
                )
            )
    finally:
        db.close()
    days = max((time.time() - since) / 86400, 1 / 24)
    per_day = len(answers) / days
    scores = store.Store(cfg.store_path)
    spent = [
        float(r["cost_eur"])
        for r in scores.rows(since)
        if r["state"] == "scored" and r["model"] != "code"
    ]
    print(f"{len(answers)} finished answers since {args.since} ({per_day:.0f} per day)")
    if estimates:
        mean = statistics.fmean(estimates) * cfg.usd_to_eur
        print(
            f"reserved upper bound per answer (bulk model, {len(estimates)} sampled): mean {mean:.4f} EUR, "
            f"max {max(estimates) * cfg.usd_to_eur:.4f} EUR -> {mean * per_day:.2f} EUR per day"
        )
    if spent:
        actual = statistics.fmean(spent)
        print(
            f"measured cost per scored answer: {actual:.4f} EUR over {len(spent)} -> {actual * per_day:.2f} EUR per day"
        )
    print(
        f"daily cap {cfg.daily_cap_eur:.2f} EUR, monthly cap {cfg.month_cap_eur:.2f} EUR (shared ledger)"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tools.eval.live_judge")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser(
        "serve", help="poll finished answers, judge them, serve the scores"
    )
    p.add_argument("--key-file", type=Path, default=engine.DEFAULT_KEY_FILE)
    p.set_defaults(func=cmd_serve)
    p = sub.add_parser("backfill", help="judge earlier answers (own run cap)")
    p.add_argument("--since", required=True)
    p.add_argument("--until")
    p.add_argument(
        "--cap", type=float, default=2.0, help="EUR for this backfill (default 2)"
    )
    p.add_argument("--workers", type=int, default=3)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--key-file", type=Path, default=engine.DEFAULT_KEY_FILE)
    p.set_defaults(func=cmd_backfill)
    p = sub.add_parser("report", help="history: summary and the worst answers")
    p.add_argument("--since", default="1d")
    p.add_argument("--worst", type=int, default=20)
    p.add_argument("--version")
    p.add_argument("--class", dest="cls")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_report)
    p = sub.add_parser("summary", help="write the daily and per-version summary files")
    p.add_argument("--day")
    p.set_defaults(func=cmd_summary)
    p = sub.add_parser(
        "estimate", help="cost per answer and per day at the observed volume"
    )
    p.add_argument("--since", default="1d")
    p.add_argument("--sample", type=int, default=40)
    p.set_defaults(func=cmd_estimate)
    args = ap.parse_args(argv)
    try:
        return int(args.func(args))
    except guard.NotStaging as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
