"""Per-run report, calibration review file and agreement measurement."""

from __future__ import annotations

import datetime as dt
import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .adapters import Item
from .judge import Result, Task, missing_evidence

ADVISORY = (
    "Advisory only: the judge's agreement with the owner's labels has not been measured yet, "
    "so nothing in this report gates a merge, a deploy or a promotion."
)


def cadence_per_day(folder: Path, pattern: str, days: int = 7) -> float:
    if not folder.exists():
        return 0.0
    cutoff = dt.datetime.now(dt.UTC).timestamp() - days * 86400
    return sum(1 for p in folder.glob(pattern) if p.stat().st_mtime >= cutoff) / days


def code_disagreement(verdict: str, checks: list[tuple[str, str, str]]) -> str | None:
    statuses = {s for _, s, _ in checks}
    if verdict == "fail" and "PASS" in statuses and "FAIL" not in statuses:
        return "judge fail, code checks pass"
    if verdict == "pass" and "FAIL" in statuses:
        return "judge pass, a code check fails"
    return None


def build_report(
    run_id: str,
    items: dict[str, Item],
    tasks: dict[str, Task],
    local: dict[str, Result],
    bulk: dict[str, Result],
    arbiter: dict[str, Result],
    notes: list[str],
    ledger_totals: tuple[float, float],
    caps: dict[str, float],
    usd_to_eur: float,
    cadence: dict[str, float],
) -> tuple[str, dict[str, Any]]:
    final: dict[str, Result] = {**local, **bulk, **arbiter}
    lines = [f"# Frontier judge report {run_id}", "", ADVISORY, ""]
    spent = [r for r in [*bulk.values(), *arbiter.values()]]
    by_model: dict[str, list[Result]] = defaultdict(list)
    for r in spent:
        by_model[f"{r.model} ({r.mode})"].append(r)
    served = sorted({r.served_model for r in spent if r.served_model})
    lines += [
        "## Judge models and cost",
        "",
        f"Served model ids: {', '.join(served) or 'none'}",
        "",
    ]
    lines += [
        "| model | requests | input tokens | output tokens | USD | EUR |",
        "|---|---|---|---|---|---|",
    ]
    total_usd = 0.0
    for name, rs in sorted(by_model.items()):
        usd = sum(r.usd for r in rs)
        total_usd += usd
        lines.append(
            f"| {name} | {len(rs)} | {sum(r.input_tokens for r in rs)} | {sum(r.output_tokens for r in rs)} | {usd:.4f} | {usd * usd_to_eur:.4f} |"
        )
    month_eur, run_eur = ledger_totals
    lines += [
        "",
        f"Run total: {total_usd:.4f} USD = {total_usd * usd_to_eur:.4f} EUR at {usd_to_eur} EUR/USD "
        f"(ledger run total {run_eur:.4f} EUR, cap {caps['run']:.2f}).",
        f"Month to date (ledger, all runs): {month_eur:.4f} EUR of the {caps['month']:.2f} EUR cap.",
    ]
    # projection at the current cadence
    per_source: dict[str, float] = defaultdict(float)
    for r in spent:
        task = tasks.get(r.task_id)
        if task:
            per_source[task.source] += r.usd
    battery_runs = {s for s in per_source if not s.startswith("trip_")}
    trip_runs = {s for s in per_source if s.startswith("trip_")}
    per_battery = sum(per_source[s] for s in battery_runs) / max(len(battery_runs), 1)
    per_trip = sum(per_source[s] for s in trip_runs) / max(len(trip_runs), 1)
    month_projection = (
        (per_battery * cadence["battery"] + per_trip * cadence["trip"])
        * 30
        * usd_to_eur
    )
    lines += [
        f"Projected monthly cost at the current cadence: {month_projection:.2f} EUR "
        f"({per_battery * usd_to_eur:.3f} EUR per battery run x {cadence['battery']:.2f} runs/day, "
        f"{per_trip * usd_to_eur:.3f} EUR per trip run x {cadence['trip']:.2f} runs/day, 30 days).",
        "",
    ]
    if notes:
        lines += ["## Run notes", "", *[f"- {n}" for n in notes], ""]

    verdict_ids = [t for t in final if not t.startswith("pair::")]
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for tid in verdict_ids:
        counts[tid.rsplit("::", 1)[1]][final[tid].verdict] += 1
    lines += [
        "## Verdicts per class",
        "",
        "| class | pass | fail | unknown |",
        "|---|---|---|---|",
    ]
    for cls in sorted(counts, key=lambda c: int(c[1:])):
        c = counts[cls]
        lines.append(f"| {cls} | {c['pass']} | {c['fail']} | {c['unknown']} |")
    lines.append("")

    disagreements: list[tuple[str, str]] = []
    for tid in verdict_ids:
        item_id, cls = tid.rsplit("::", 1)
        d = code_disagreement(
            final[tid].verdict, items[item_id].code_checks.get(cls, [])
        )
        if d:
            disagreements.append((tid, d))
    lines += ["## Judge versus code checks", ""]
    if not disagreements:
        lines.append("No disagreement with an existing code check.")
    for tid, d in disagreements:
        item_id, cls = tid.rsplit("::", 1)
        checks = "; ".join(
            f"{n} {s}" for n, s, _ in items[item_id].code_checks.get(cls, [])
        )
        lines.append(f"- {tid}: {d} ({checks})")
    lines.append("")

    lines += ["## Failing items", ""]
    for tid in verdict_ids:
        r = final[tid]
        if r.verdict != "fail":
            continue
        lines.append(
            f"### {tid} ({r.served_model or r.model}, confidence {r.confidence:.2f})"
        )
        lines.append("")
        lines.append(r.reason)
        for ev in r.evidence[:4]:
            lines.append(f"> [{ev.get('source', '')}] {ev.get('quote', '')[:400]}")
        if r.unverified_quotes:
            lines.append(
                f"Quotes not found verbatim in the evidence: {len(r.unverified_quotes)}"
            )
        lines.append("")

    pair_rows: list[dict[str, Any]] = []
    pair_ids = sorted({t.rsplit("::", 1)[0] for t in final if t.startswith("pair::")})
    if pair_ids:
        lines += [
            "## Pairwise comparisons (both orders)",
            "",
            "| class | A | B | A-first order | B-first order | consistent |",
            "|---|---|---|---|---|---|",
        ]
    for pid in pair_ids:
        ab, ba = final.get(pid + "::ab"), final.get(pid + "::ba")
        _, cls, pair = pid.split("::", 2)
        x, y = pair.split("|")
        first = ab.verdict if ab else "missing"
        second = {"A": "B", "B": "A"}.get(ba.verdict, ba.verdict) if ba else "missing"
        consistent = first == second
        pair_rows.append(
            {
                "class": cls,
                "a": x,
                "b": y,
                "ab": first,
                "ba_mapped": second,
                "consistent": consistent,
            }
        )
        lines.append(
            f"| {cls} | {x} | {y} | {first} | {second} | {'yes' if consistent else 'no'} |"
        )
    if pair_ids:
        lines.append("")

    sample = [t for t in arbiter if t in bulk]
    agree = sum(1 for t in sample if arbiter[t].verdict == bulk[t].verdict)
    lines += [
        "## Bulk judge versus arbiter",
        "",
        f"Re-judged by the arbiter: {len(sample)} tasks; same verdict on {agree}.",
    ]
    for t in sample:
        if arbiter[t].verdict != bulk[t].verdict:
            lines.append(
                f"- {t}: bulk {bulk[t].verdict} ({bulk[t].confidence:.2f}), arbiter {arbiter[t].verdict} ({arbiter[t].confidence:.2f})"
            )
    unverified = [(t, q) for t, r in final.items() for q in r.unverified_quotes]
    lines += [
        "",
        f"Quotes not found verbatim in the evidence (final verdicts): {len(unverified)}",
        "",
    ]
    for t, q in unverified[:15]:
        lines.append(f"- {t}: {q[:160]!r}")
    errors = [(t, r.error) for t, r in {**bulk, **arbiter}.items() if r.error]
    if errors:
        lines += ["", "## Request errors", "", *[f"- {t}: {e}" for t, e in errors[:20]]]
    data = {
        "run_id": run_id,
        "advisory_only": True,
        "served_models": served,
        "cost_usd": total_usd,
        "cost_eur": total_usd * usd_to_eur,
        "month_to_date_eur": month_eur,
        "projected_month_eur": month_projection,
        "counts": {k: dict(v) for k, v in counts.items()},
        "disagreements": disagreements,
        "pairs": pair_rows,
        "results": {t: asdict(r) for t, r in final.items()},
        "bulk": {t: asdict(r) for t, r in bulk.items()},
        "arbiter": {t: asdict(r) for t, r in arbiter.items()},
        "notes": notes,
    }
    return "\n".join(lines) + "\n", data


def _short(text: str, n: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= n else text[:n] + " [...]"


def write_review(
    items: list[Item],
    rubrics: dict[str, dict[str, Any]],
    classes: list[str],
    per_class: int,
    path: Path,
) -> dict[str, int]:
    lines = [
        "# Frontier judge calibration review",
        "",
        "For each entry, read the evidence files, then replace `label:` with `label: pass`, `label: fail` or "
        "`label: unknown` for the class named in the entry (one binary question per entry). The judge's own "
        "verdicts are not shown here, so they cannot anchor the label. Paths are on the build VM.",
        "",
    ]
    counts: dict[str, int] = {}
    ordered = sorted(items, key=lambda it: (it.source, it.item_id), reverse=True)
    for cls in classes:
        rubric = rubrics[cls]
        chosen = [
            it
            for it in ordered
            if cls in it.classes and not missing_evidence(it, rubric)
        ][:per_class]
        counts[cls] = len(chosen)
        lines += [
            f"## {cls} {rubric['name']} ({len(chosen)} entries)",
            "",
            f"Question: {rubric['question']}",
            "",
        ]
        for n, it in enumerate(chosen, 1):
            lines.append(f"### {cls}-{n:03d}")
            lines.append(f"- task: {it.item_id}::{cls}")
            for ev in it.evidence:
                if ev.kind in ("request", "answer", "answer_excerpt"):
                    lines.append(f"- {ev.kind}: {_short(ev.text or '', 500)}")
            for ev in it.evidence:
                if ev.path:
                    lines.append(f"- evidence ({ev.kind}): {ev.path}")
            for ev in it.evidence:
                if ev.kind in (
                    "artifact_record",
                    "source_urls",
                    "privacy_record",
                    "render_checks",
                ):
                    lines.append(f"- {ev.kind}: {_short(ev.text or '', 400)}")
            for name, status, _ in it.code_checks.get(cls, []):
                lines.append(f"- code check: {name} {status}")
            for note in it.notes:
                lines.append(f"- note: {note}")
            lines += ["label:", "owner note:", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return counts


def read_labels(path: Path) -> dict[str, str]:
    labels: dict[str, str] = {}
    task = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("- task: "):
            task = line[len("- task: ") :].strip()
        elif line.startswith("label:") and task:
            value = line[len("label:") :].strip().lower()
            if value in ("pass", "fail", "unknown"):
                labels[task] = value
            task = None
    return labels


def agreement(
    labels: dict[str, str], results: dict[str, dict[str, Any]]
) -> dict[str, dict[str, float]]:
    """Per-class raw agreement and Cohen's kappa between owner labels and judge verdicts."""
    per: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for task, label in labels.items():
        if task in results:
            per[task.rsplit("::", 1)[1]].append((label, str(results[task]["verdict"])))
    out: dict[str, dict[str, float]] = {}
    for cls, pairs in per.items():
        n = len(pairs)
        po = sum(1 for a, b in pairs if a == b) / n
        cats = {"pass", "fail", "unknown"}
        pe = sum(
            (sum(1 for a, _ in pairs if a == c) / n)
            * (sum(1 for _, b in pairs if b == c) / n)
            for c in cats
        )
        kappa = (po - pe) / (1 - pe) if pe < 1 else 1.0
        out[cls] = {"n": float(n), "agreement": po, "kappa": kappa}
    return out


def dump(path: Path, data: Any) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
    )
