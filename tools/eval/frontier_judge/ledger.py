"""Spend ledger with a hard monthly cap and a per-run cap.

Every request is reserved before it is sent (upper-bound estimate) and settled with the
usage fields of the response. Open reservations count as spent, so a crash between
sending and settling never lets the next run exceed the cap.
"""

from __future__ import annotations

import datetime as dt
import fcntl
import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Price:
    input_usd_per_m: float
    output_usd_per_m: float

    def usd(self, input_tokens: int, output_tokens: int) -> float:
        return (
            input_tokens * self.input_usd_per_m + output_tokens * self.output_usd_per_m
        ) / 1_000_000


def price_for(models: dict[str, Any], model: str, mode: str) -> Price:
    entry = models["models"][model][mode]
    return Price(float(entry["input"]), float(entry["output"]))


class BudgetExceeded(Exception):
    pass


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class Ledger:
    def __init__(
        self,
        path: Path,
        run_id: str,
        month_cap_eur: float,
        run_cap_eur: float,
        usd_to_eur: float,
        now: Callable[[], dt.datetime] = _utcnow,
    ) -> None:
        self.path = path
        self.run_id = run_id
        self.month_cap_eur = month_cap_eur
        self.run_cap_eur = run_cap_eur
        self.usd_to_eur = usd_to_eur
        self.now = now
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)

    @contextmanager
    def _locked(self) -> Iterator[Any]:
        with open(self.path, "a+", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield fh
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def _entries(self, fh: Any) -> list[dict[str, Any]]:
        fh.seek(0)
        out: list[dict[str, Any]] = []
        for line in fh.read().splitlines():
            if line.strip():
                out.append(json.loads(line))
        return out

    def _totals(self, entries: list[dict[str, Any]]) -> tuple[float, float]:
        month = self.now().strftime("%Y-%m")
        settled = {e["key"] for e in entries if e["kind"] == "settle"}
        month_eur = run_eur = 0.0
        for e in entries:
            counted = e["kind"] == "spend" or (
                e["kind"] == "reserve" and e["key"] not in settled
            )
            if not counted:
                continue
            if e["month"] == month:
                month_eur += float(e["eur"])
            if e["run"] == self.run_id:
                run_eur += float(e["eur"])
        return month_eur, run_eur

    def totals(self) -> tuple[float, float]:
        """(month-to-date EUR, this run EUR), open reservations included."""
        with self._locked() as fh:
            return self._totals(self._entries(fh))

    def _append(self, fh: Any, entry: dict[str, Any]) -> None:
        now = self.now()
        entry = {
            "ts": now.isoformat(),
            "month": now.strftime("%Y-%m"),
            "run": self.run_id,
            **entry,
        }
        fh.seek(0, 2)
        fh.write(json.dumps(entry, sort_keys=True) + "\n")
        fh.flush()

    def reserve(self, key: str, usd_estimate: float, note: str = "") -> None:
        eur = usd_estimate * self.usd_to_eur
        with self._locked() as fh:
            month_eur, run_eur = self._totals(self._entries(fh))
            if month_eur + eur > self.month_cap_eur:
                raise BudgetExceeded(
                    f"monthly cap {self.month_cap_eur:.2f} EUR: {month_eur:.2f} spent or reserved, {eur:.2f} more needed"
                )
            if run_eur + eur > self.run_cap_eur:
                raise BudgetExceeded(
                    f"run cap {self.run_cap_eur:.2f} EUR: {run_eur:.2f} spent or reserved, {eur:.2f} more needed"
                )
            self._append(
                fh,
                {
                    "kind": "reserve",
                    "key": key,
                    "eur": round(eur, 6),
                    "usd": round(usd_estimate, 6),
                    "note": note,
                },
            )

    def spend(
        self,
        key: str,
        model: str,
        mode: str,
        price: Price,
        input_tokens: int,
        output_tokens: int,
    ) -> float:
        usd = price.usd(input_tokens, output_tokens)
        with self._locked() as fh:
            self._append(
                fh,
                {
                    "kind": "spend",
                    "key": key,
                    "model": model,
                    "mode": mode,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "usd": round(usd, 6),
                    "eur": round(usd * self.usd_to_eur, 6),
                },
            )
        return usd

    def settle(self, key: str) -> None:
        with self._locked() as fh:
            self._append(fh, {"kind": "settle", "key": key, "eur": 0.0, "usd": 0.0})

    def history(self) -> list[dict[str, Any]]:
        with self._locked() as fh:
            return self._entries(fh)
