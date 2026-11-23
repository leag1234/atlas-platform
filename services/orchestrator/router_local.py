"""Local routing classifier: the decision of a turn no profile decided in time.

When every routing call fails or misses its bound, the turn used to fall back
to the word patterns of the older gates, which misrouted any phrasing nobody
had foreseen (a change to an app asked as a question, a run request with a
typo). This classifier replaces them: a complement naive Bayes model (Rennie
et al., ICML 2003) over character n-grams of the folded message, so that
typos, missing accents and inflections still share most features, trained at
first use on router_examples.jsonl: synthetic messages written by a model from
a description of each kind, in ten languages, never taken from a conversation
(tools/router_synth.py). Training takes well under a second and needs no
dependency; nothing is downloaded.

What the assistant delivered just before (an app, a file, a script) is one
more feature, read from the conversation as the routing call reads it, so that
"make the buttons bigger" after an app stays an app change.
"""

from __future__ import annotations

import json
import math
import re
import threading
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path

EXAMPLES = Path(__file__).with_name("router_examples.jsonl")
KINDS = ("answer", "research", "drawing", "app", "file", "run", "remote", "other")
# The kind a follow-up keeps after each delivered context.
CONTEXT_KIND = {
    "app": "app",
    "drawing": "drawing",
    "file": "file",
    "run": "run",
    "script": "run",
    "remote": "remote",
}
# Smoothing and the weight of the delivered-context feature, chosen on the
# synthetic set (tools/router_bench.py --local reports the held-out cases).
ALPHA = 0.5
CONTEXT_WEIGHT = 6.0

_WORD = re.compile(r"\w+")
_FENCE = re.compile(r"```")
_lock = threading.Lock()


def fold(text: str) -> str:
    """Case, accents and spacing folded: a word typed without its accents matches."""
    return "".join(
        character
        for character in unicodedata.normalize("NFKD", text.casefold())
        if not unicodedata.combining(character)
    )


def features(text: str, context: str) -> dict[str, float]:
    """Character 2- to 5-grams of each word with its boundaries, the words, the context."""
    found: Counter[str] = Counter()
    for word in _WORD.findall(fold(text))[:80]:
        found["w:" + word] += 1
        padded = f"_{word}_"
        for size in (2, 3, 4, 5):
            for start in range(max(1, len(padded) - size + 1)):
                found[padded[start : start + size]] += 1
    # Sublinear counts: a repeated word does not outweigh the rest.
    weighted = {name: 1.0 + math.log(count) for name, count in found.items()}
    weighted["c:" + (context or "none")] = CONTEXT_WEIGHT
    return weighted


class Model:
    """Complement naive Bayes: each kind is scored against all the other kinds."""

    def __init__(self, rows: Sequence[Mapping[str, str]]) -> None:
        totals: dict[str, dict[str, float]] = {kind: {} for kind in KINDS}
        for row in rows:
            kind = row["kind"]
            if kind in totals:
                counts = totals[kind]
                for name, value in features(
                    row["text"], row.get("context", "")
                ).items():
                    counts[name] = counts.get(name, 0.0) + value
        vocabulary: set[str] = set().union(*totals.values())
        everything: dict[str, float] = {}
        for counts in totals.values():
            for name, value in counts.items():
                everything[name] = everything.get(name, 0.0) + value
        grand = sum(everything.values())
        size = len(vocabulary)
        self.weights: dict[str, dict[str, float]] = {}
        self.default: dict[str, float] = {}
        for kind in KINDS:
            rest_total = grand - sum(totals[kind].values())
            denominator = rest_total + ALPHA * size
            self.default[kind] = -math.log(ALPHA / denominator)
            self.weights[kind] = {
                name: -math.log(
                    (everything[name] - totals[kind].get(name, 0.0) + ALPHA)
                    / denominator
                )
                for name in vocabulary
            }
        # Normalised weights (Rennie et al., section 3.2): no kind wins by length.
        for kind in KINDS:
            norm = sum(abs(value) for value in self.weights[kind].values()) or 1.0
            scale = size / norm
            self.weights[kind] = {
                name: value * scale for name, value in self.weights[kind].items()
            }
            self.default[kind] *= scale

    def scores(self, text: str, context: str) -> dict[str, float]:
        found = features(text, context)
        return {
            kind: sum(
                count * self.weights[kind].get(name, 0.0)
                for name, count in found.items()
                if name in self.weights[kind]
            )
            for kind in KINDS
        }

    def kind(self, text: str, context: str) -> str:
        scores = self.scores(text, context)
        return max(KINDS, key=lambda kind: scores[kind])


_trained: list[Model] = []
_warming = threading.Event()


def warm() -> None:
    """Train the model in the background, once, so a fallback finds it ready."""
    if _trained or _warming.is_set():
        return
    _warming.set()
    threading.Thread(target=_warm, name="router-local-warm", daemon=True).start()


def _warm() -> None:
    try:
        model()
    except Exception:  # noqa: BLE001 - the fallback reports it when it is needed
        _warming.clear()


def model() -> Model:
    """The trained model, built once per process (concurrent callers wait)."""
    with _lock:
        if not _trained:
            rows = [
                json.loads(line)
                for line in EXAMPLES.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            _trained.append(Model(rows))
        return _trained[0]


def context(history: Sequence[Mapping[str, object]]) -> str:
    """What the last assistant turn delivered, as the routing call sees it."""
    from services.orchestrator import request_router

    for turn in reversed(history):
        if turn.get("role") != "assistant":
            continue
        content = turn.get("content")
        if not isinstance(content, str):
            return ""
        marks = request_router._delivered(content)
        if "interactive app" in marks:
            return "app"
        if "a picture" in marks:
            return "drawing"
        if "a file" in marks:
            return "file"
        if "set up the user's machine" in marks:
            return "remote"
        if len(_FENCE.findall(content)) >= 2:
            return "script"
        return ""
    return ""


def decide(text: str, history: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """The routing decision fields for this turn, from the local model alone."""
    delivered = context(history)
    kind = model().kind(text, delivered)
    decision: dict[str, object] = {
        "kind": kind,
        "follow_up": bool(delivered) and CONTEXT_KIND.get(delivered) == kind,
    }
    if kind == "file":
        from services.orchestrator.request_router import named_formats

        found = named_formats(text)
        decision["file_format"] = found[0] if len(found) == 1 else None
    return decision
