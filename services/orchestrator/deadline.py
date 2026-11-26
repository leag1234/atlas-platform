"""Inference deadline with a separately bounded infrastructure startup window."""

import asyncio
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from collections.abc import AsyncIterator, Callable, Iterator
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services.orchestrator.chat_schema import ChatRequest
import time

_current: ContextVar[asyncio.Timeout | None] = ContextVar(
    "request_deadline", default=None
)
_started: ContextVar[float | None] = ContextVar("request_deadline_start", default=None)
# The seconds a chat answer written after research leaves before the
# deadline, for the checks that follow it and for an answer written again
# from the pages read when it fails (a trip: travel_rebuild.recover). Read
# when the answer call starts: a turn knows only then what it read.
_recovery: ContextVar[Callable[[], float] | None] = ContextVar(
    "answer_recovery", default=None
)


@contextmanager
def answer_recovery(seconds: Callable[[], float]) -> Iterator[None]:
    token = _recovery.set(seconds)
    try:
        yield
    finally:
        _recovery.reset(token)


def recovery_seconds() -> float:
    """What the current turn keeps after its answer call (0 without a recovery)."""
    reserve = _recovery.get()
    return max(0.0, reserve()) if reserve is not None else 0.0


@asynccontextmanager
async def request_deadline(seconds: float) -> AsyncIterator[None]:
    started = asyncio.get_running_loop().time()
    async with asyncio.timeout(seconds) as deadline:
        token = _current.set(deadline)
        started_token = _started.set(started)
        try:
            yield
        finally:
            _current.reset(token)
            _started.reset(started_token)


def remaining() -> float | None:
    """Seconds left before the current request deadline, or None without one."""
    deadline = _current.get()
    when = deadline.when() if deadline is not None else None
    if when is None:
        return None
    return max(0.0, when - asyncio.get_running_loop().time())


def extend(seconds: float) -> None:
    """Move the current request deadline later, for a turn routed after it was set.

    The deadline is set from ChatRequest.timeout_seconds before the turn runs; a
    model routing decision taken inside the turn can make it a 300 s turn.
    """
    deadline = _current.get()
    if seconds <= 0 or deadline is None or deadline.expired():
        return
    when = deadline.when()
    if when is not None:
        deadline.reschedule(when + seconds)


def extend_for_path(request: "ChatRequest", total: float | None = None) -> None:
    """Give a late handoff the deadline its direct route would have had.

    A turn can be read as a plain chat answer, then handed to Work, an app or a
    file path after the deadline was set. The handoff must never shorten the
    deadline; it moves it to the full allowance of the path, minus the time
    already spent. `total` gives that allowance when the request alone does
    not show the new path (a plain answer handed to Work after routing).
    """
    deadline = _current.get()
    before = deadline.when() if deadline is not None else None
    started = _started.get()
    if deadline is None or before is None or started is None:
        return
    wanted = started + (request.timeout_seconds if total is None else total)
    if wanted > before:
        deadline.reschedule(wanted)


@asynccontextmanager
async def infrastructure_startup(seconds: float) -> AsyncIterator[None]:
    """Only GPU readiness can use this window; inference keeps its old allowance."""
    deadline = _current.get()
    original = deadline.when() if deadline is not None else None
    started = time.monotonic()
    if deadline is not None and original is not None:
        deadline.reschedule(original + seconds)
    try:
        async with asyncio.timeout(seconds):
            yield
    finally:
        if deadline is not None and original is not None and not deadline.expired():
            deadline.reschedule(original + min(seconds, time.monotonic() - started))
