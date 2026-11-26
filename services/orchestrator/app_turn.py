"""App and drawing turns for the side panel.

The model writes the whole file as a fenced block (```html or ```svg) in its
ordinary, streamed answer. Atlas extracts the last complete block, validates
it, stores it as the artifact of the conversation and answers with a short
description and the panel link. No tool call carries the file and the model
never names an artifact: a follow-up changes the conversation's current
artifact, whose source Atlas passes to the model.

Measured on staging (2026-11-11) with the previous design, where the file was
the JSON argument of a create_artifact call: 8 of 18 corpus apps failed
(invalid arguments, invented artifact ids, technical text), and the user saw
only a status for a median 53 s.
"""

from __future__ import annotations

import asyncio
import dataclasses
import functools
import logging
import os
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

import aiohttp
from pydantic import ValidationError

from packages import artifact_media, privacy
from packages.artifact_html import Problem
from packages.artifact_media import Media
from packages.artifact_source import (
    LineCounter,
    described,
    drawing_source,
    filename_for,
    is_drawing,
    last_block,
    prepare,
    prose,
    title_of,
)
from packages.addressing import answer_instruction
from packages.language import LANGUAGES
from packages.limits import LimitError
from packages.ports import atlas_ports
from packages.user_errors import (
    app_failure,
    app_lost_features,
    app_quota,
    app_render_failure,
    app_soft_defects,
)
from services.orchestrator import (
    app_facts,
    app_features,
    app_media,
    app_render,
    artifact_store,
    expectations,
)
from services.orchestrator.artifact_store import ArtifactError
from services.orchestrator.artifact_tools import (
    PROBLEM_DETAILS,
    CurrentArtifact,
    compose_answer,
    identical_answer,
)
from services.orchestrator.chat_schema import ChatRequest
from services.orchestrator.interactions import Interaction
from services.orchestrator.loop import GatewayError, Message, Turn
from services.orchestrator.model import GatewayModel
from services.orchestrator.stream_client import Sink, sink_context
from services.orchestrator.work_plan import ChatPlan

Kind = Literal["app", "drawing"]

# Output room of one app or drawing call. The gateway accepts up to
# APP_OUTPUT_CEILING tokens for a file turn (agent_provider.py); the per-call
# cost ceiling of a file turn (0.30 EUR) still bounds every attempt.
APP_OUTPUT_TOKENS = 16000
APP_OUTPUT_CEILING = 16000
# One generation and one repair (invalid file, no file, or a failed call).
MAX_CALLS = 2
# Time kept for validation, storage and the answer after the last call.
DEADLINE_MARGIN = 8.0
# A call that cannot write a file in less is not started.
MINIMUM_CALL_SECONDS = 25.0
# A provider failure that a new call may not meet (a broken stream, a refusal for
# capacity, a 5xx) is tried again after these waits while the turn's time allows,
# without using one of the MAX_CALLS writes. Staging 2026-11-24: three 429 answers
# in 4 s used every write of an app turn, which then asked the person to resend
# with about 120 s of its time left.
TRANSIENT_FAILURES = frozenset(
    {
        "provider_error",
        "gateway_stream_error",
        "gateway_unavailable",
        "incomplete_stream",
        "invalid_stream",
        "empty_completion",
        "incomplete_provider_answer",
    }
)
TRANSIENT_WAITS = (2.0, 5.0, 10.0)
PROGRESS_SECONDS = 1.0
HISTORY_MESSAGES = 12
HISTORY_CHARS = 6000
SUMMARY_CHARS = 700
MAX_PROBLEMS = 10
MAX_DETAIL_CHARS = 80
# D5: budget and quota refusals keep their measured values and their own path.
BUDGET_CODES = frozenset(
    {"cost_budget", "cost", "daily_budget", "quota_exhausted", "quota_exceeded"}
)

_FILE_RULES = """\
- Self-contained: inline all CSS and JavaScript. No CDN, no external URL, no font or
  image download, no network request of any kind. Draw with canvas, SVG or CSS and
  quote every attribute value (viewBox="0 0 400 300", stroke-width="2"); embed small
  assets as data: URIs. The preview blocks all network access.
- Nothing is kept: data is lost on reload and storage may be unavailable. Keep state
  in memory and wrap any storage access in try/catch. Never reload or navigate the
  page, never open windows; for forms, call preventDefault.
- Never use alert, confirm or prompt; show messages, game over and questions inside
  the page.
- Games end with a clear in-page message (the user-language equivalent of "Game over"
  with the score) and a visible restart button.
- Interface language: the user's language (lang attribute, <title> and every visible
  label). Code identifiers and comments stay in English.
- Usable at once: short on-screen instructions; keyboard controls when relevant and
  touch controls (buttons or swipe) for phones; layout from 360 px wide; visible
  focus; good contrast.
- Sound: generate it with the Web Audio API (oscillators, noise buffers). Browsers keep
  sound silent until the user's first click or key: create the AudioContext (or call
  resume()) and start any media inside a click, touch or key handler, never when the
  page loads or from a timer before the first press.
- Pictures: every <img> has a source that loads (an embedded data: URI, or an
  atlas-media reference Atlas lists); never leave an <img> without src, even hidden:
  create or show it only when there is a picture.
- Compact code: loops and small functions instead of repeated markup, no dead code,
  few comments. Everything that is asked works.
- A requested language, framework or platform is always used; never substitute
  another one silently. If the result cannot run inside Atlas, say so in one sentence
  after the block and give the command to run it locally.
"""

APP_INSTRUCTIONS = (
    """\
App mode. You write small interactive web apps (game, tool, calculator, timer, quiz,
chart, music toy, small page) that run in the Atlas side panel.
Answer format, strictly:
1. The complete app as ONE self-contained HTML file in a single fenced code block that
   starts with ```html and ends with ```. Begin the block at once: no introduction,
   no plan and no code before it.
2. After the block, at most three short sentences in the user's language for a
   non-technical user: what the app does, how to use it, the main assumptions. No
   code terms, no milliseconds or frame rates. Never write a second block.
Atlas stores the file and shows it to the user; there is no tool to call.
The file:
- <!DOCTYPE html>, <html lang="..."> in the user's language, and a <title> of at
  most 60 characters in the user's language naming the app.
"""
    + _FILE_RULES
    + """\
- Do not ask questions you can settle yourself: make reasonable choices. Ask (one
  short question, without a block) only when an essential choice cannot be made.
"""
)

DRAWING_INSTRUCTIONS = """\
Drawing mode. You draw pictures as SVG code shown in the Atlas side panel.
Answer format, strictly:
1. The complete drawing as ONE SVG document in a single fenced code block that starts
   with ```svg and ends with ```. Begin the block at once: no introduction before it.
2. After the block, one or two short sentences in the user's language describing the
   drawing, without code terms. Never write a second block.
Atlas stores the drawing and shows it to the user; there is no tool to call.
The SVG:
- <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 W H"> with a <title> of at most
  60 characters in the user's language.
- Quote every attribute value (viewBox="0 0 400 300", stroke-width="2"). Escape & as
  &amp; in text.
- Shapes, paths, gradients and text only: no external image, font or URL, no script,
  no foreignObject.
- Draw with care: a recognizable subject, a composed scene (background first), pleasant
  colors, and every element and count the user asks for (legs, windows, people).
- Text inside the picture is in the user's language.
- Never refuse and never say that you cannot draw: if a photograph or a painting is
  asked for, draw the best SVG illustration of it. Do not ask questions; make
  reasonable choices.
"""

CURRENT_SOURCE = {
    "app": (
        "The current app of this conversation (version {version}) is below. To change "
        "it, apply the user's request and write the COMPLETE new file in one ```html "
        "block: all of it, not only the changed part. Keep everything the user did not "
        "ask to change exactly as it is written there: every feature, text, element, "
        "embedded picture or sound and the code that already works; change only what "
        "the request needs. Web addresses from earlier messages that the app does "
        "not embed cannot be loaded by the preview, now as before: never write "
        "them. After the block, describe only what the new file does. If "
        "the user only asks a question about the app, answer in a few sentences "
        "without any block.\n```html\n{source}\n```"
    ),
    "drawing": (
        "The current drawing of this conversation (version {version}) is below. To "
        "change it, apply the user's request and write the COMPLETE new SVG in one "
        "```svg block. Keep everything the user did not ask to change. If the user only "
        "asks a question about the drawing, answer in a few sentences without any "
        "block.\n```svg\n{source}\n```"
    ),
}
PREVIOUS_UNAVAILABLE = (
    "The previous {noun} of this conversation is no longer available (stored for 30 "
    "days, or too large to edit). Write a new complete file that does what the user "
    "asks."
)
# After the block the model describes what the person receives, not its fix.
# Staging 2026-11-20: the description of a rejected file that loaded samples
# from the web ("three banks of real samples") was shown with the repaired
# file, which had none.
DESCRIBE = (
    "then at most three short sentences for the user describing what this file "
    "shows or does (for an app: what it does and how to use it), not the fix."
)
NO_BLOCK_REPAIR = (
    "Your answer did not contain the file. Write the complete file now, in one fenced "
    "```{fence} block, " + DESCRIBE
)
INVALID_REPAIR = (
    "Atlas rejected this file:\n{problems}\n{network}Fix every listed problem and "
    "write the complete corrected file in one fenced ```{fence} block, " + DESCRIBE
)
NETWORK_PROBLEMS = frozenset({"network_api", "external_reference"})
# The person's files (app_media.py, packages/artifact_media.py): the model
# writes references, Atlas embeds the bytes when it stores the file.
MEDIA_FILES = (
    "Atlas downloaded these files named by the user; they are embedded in the app "
    "file when it is stored. Use them where the user asked, writing each reference "
    "exactly as below, as a string:\n{lines}\n"
    '- In HTML and CSS the reference is a URL: <img src="atlas-media:NAME">, '
    '<audio src="atlas-media:NAME">, url(atlas-media:NAME).\n'
    "- For Web Audio, decode it with ctx.decodeAudioData("
    'atlasMedia.buffer("atlas-media:NAME")); atlasMedia is provided by Atlas and '
    "returns an ArrayBuffer. Create the AudioContext in the first click or key "
    "handler (to decode at load, use an OfflineAudioContext). Never fetch a "
    "reference and never write other addresses for these files."
)
MEDIA_FAILED = (
    "These files named by the user could not be downloaded, so the app cannot use "
    "them: {urls}. Make the app work without them (for sounds, synthesize them with "
    "the Web Audio API) and say so in one of the sentences after the block."
)
NETWORK_REPAIR = (
    "The preview cannot load anything from the network: files from other sites "
    "(sounds, images, fonts, libraries) cannot be used. Make the app work without "
    "them; if the user asked for such files, say so plainly in one of the sentences "
    "after the block.\n"
)
RENDER_REPAIR = (
    "Atlas opened this file in the preview (a sandboxed browser page that blocks "
    "all network access, as the user's side panel does) and it does not work:\n"
    "{problems}\n{network}Fix the cause of every listed problem and write the "
    "complete corrected file in one fenced ```html block, " + DESCRIBE
)
# On a follow-up, Atlas opens the current version before the model writes the
# next one (staging -142: after "it does not work, nothing shows", the model
# guessed a cause, rewrote code that worked and said that was the problem).
CURRENT_CHECK = (
    "Atlas opened version {version} in the preview just before this turn (a "
    "sandboxed browser page without network, like the user's side panel; its "
    "timers ran {seconds} s and up to three buttons were pressed). {found}"
)
CURRENT_PROBLEMS = (
    "It saw these problems:\n{problems}\nWhen the user says something does not "
    "work, these are the measured causes: fix them and keep the rest as written."
)
CURRENT_FINE = (
    "It saw no error, no broken image and no blocked sound. When the user says "
    "something does not work, look for the cause in the file and change only what "
    "fixes it; say plainly what you changed and never claim a cause that was not "
    "found."
)
# Render checks of one turn's new files, together (the current version's own
# check runs beside the model call and is not counted). Staging -145: an app
# follow-up must stay within an ordinary chat turn.
CHECK_BUDGET_SECONDS = 20.0
# A repair of a file that works (a soft defect, a lost feature) is made only
# when the turn so far plus another call as long as the last one stays under
# this; a repair of a file that does not work is always tried.
OPTIONAL_REPAIR_SECONDS = 120.0
# A follow-up that drops features of the version it changes (app_features.py).
LOSS_REPAIR = (
    "Compared with version {version}, the version the user has now, this file no "
    "longer has:\n{losses}\nOnly the user's request (with the earlier messages) "
    "removes or replaces something. Restore every item the request did not ask to "
    "remove, as it is written in version {version}, keep the requested change, and "
    "write the complete corrected file in one fenced ```html block, " + DESCRIBE
)
LOSS_KEEP = (
    " If the request asked to remove or replace exactly these items, answer only "
    "with the word KEEP instead of a file: Atlas then stores the file you wrote."
)
KEEP_WORD = "KEEP"
ALSO_LOST = (
    "Also, compared with version {version}, this file no longer has:\n{losses}\n"
    "Restore every item the request did not ask to remove, as it is written in "
    "version {version}."
)
# Requirements of the expectations ledger the file does not meet (expectations.py).
UNMET_REPAIR = (
    "Atlas checked this file against every requirement the person stated in this "
    "conversation. These are not met:\n{lines}\nFix each one, keep every other "
    "requirement and everything else that works, and write the complete corrected "
    "file in one fenced ```html block, " + DESCRIBE
)
# Every repair of a follow-up (staging -142: the repair of a file rejected for
# its web addresses wrote the whole app again and lost what version 3 had).
FOLLOW_UP_REPAIR = (
    "\nThis file changes version {version}: start again from version {version} as "
    "it is written above, keep everything the request did not change, and never "
    "write the app again from scratch."
)
EXTRA_DETAILS = {
    "svg_syntax": "the SVG is not well-formed XML here (unclosed element, unquoted "
    "attribute value or stray character)",
    "svg_root": "the block must contain one <svg> element",
    "svg_doctype": "remove the DOCTYPE and entity declarations",
    "not_a_document": "the file must be a complete HTML document with <html> and <body>",
    "external_reference": "remove this external URL or file reference",
    "network_api": "remove this network access",
    "navigation_api": "remove this page navigation",
    "forbidden_element": "remove this element",
    "too_large": "the file is too large; make it shorter",
}

PROGRESS = {
    "app": {
        "en": (
            "Writing the app…",
            "Writing the app… {lines} lines",
            "Checking the app…",
            "Fixing the app…",
        ),
        "fr": (
            "Écriture de l'application…",
            "Écriture de l'application… {lines} lignes",
            "Vérification de l'application…",
            "Correction de l'application…",
        ),
        "de": (
            "App wird geschrieben…",
            "App wird geschrieben… {lines} Zeilen",
            "App wird geprüft…",
            "App wird korrigiert…",
        ),
        "es": (
            "Escribiendo la aplicación…",
            "Escribiendo la aplicación… {lines} líneas",
            "Comprobando la aplicación…",
            "Corrigiendo la aplicación…",
        ),
        "it": (
            "Scrittura dell'app…",
            "Scrittura dell'app… {lines} righe",
            "Verifica dell'app…",
            "Correzione dell'app…",
        ),
    },
    "drawing": {
        "en": (
            "Drawing…",
            "Drawing… {lines} lines",
            "Checking the drawing…",
            "Fixing the drawing…",
        ),
        "fr": (
            "Dessin en cours…",
            "Dessin en cours… {lines} lignes",
            "Vérification du dessin…",
            "Correction du dessin…",
        ),
        "de": (
            "Zeichnung entsteht…",
            "Zeichnung entsteht… {lines} Zeilen",
            "Zeichnung wird geprüft…",
            "Zeichnung wird korrigiert…",
        ),
        "es": (
            "Dibujando…",
            "Dibujando… {lines} líneas",
            "Comprobando el dibujo…",
            "Corrigiendo el dibujo…",
        ),
        "it": (
            "Disegno in corso…",
            "Disegno in corso… {lines} righe",
            "Verifica del disegno…",
            "Correzione del disegno…",
        ),
    },
}
DEFAULT_TITLES = {
    "app": {
        "en": "App",
        "fr": "Application",
        "de": "App",
        "es": "Aplicación",
        "it": "App",
    },
    "drawing": {
        "en": "Drawing",
        "fr": "Dessin",
        "de": "Zeichnung",
        "es": "Dibujo",
        "it": "Disegno",
    },
}


def output_tokens() -> int:
    """The output room of an app call: ATLAS_ARTIFACT_MAX_TOKENS, within the ceiling."""
    try:
        value = int(os.environ.get("ATLAS_ARTIFACT_MAX_TOKENS", APP_OUTPUT_TOKENS))
    except ValueError:
        value = APP_OUTPUT_TOKENS
    return max(1000, min(value, APP_OUTPUT_CEILING))


def instructions(kind: Kind) -> str:
    return DRAWING_INSTRUCTIONS if kind == "drawing" else APP_INSTRUCTIONS


class Progress:
    """Model sink of an app turn: the file is never streamed, its progress is."""

    def __init__(self, sink: Sink | None, kind: Kind, language: str) -> None:
        self.sink = sink
        labels = PROGRESS[kind]
        self.labels = labels.get(language, labels["en"])
        self.counter = LineCounter()
        self.reported = 0
        self.last = 0.0
        self.started = time.monotonic()
        self.first_status: float | None = None
        self.first_lines: float | None = None

    async def status(self, index: int, lines: int = 0) -> None:
        if self.sink is None:
            return
        now = time.monotonic()
        if self.first_status is None:
            self.first_status = now - self.started
        if index == 1 and self.first_lines is None:
            self.first_lines = now - self.started
        self.last = now
        await self.sink(
            {
                "phase": "artifact_progress",
                "description": self.labels[index].format(lines=lines),
            }
        )

    def restart(self) -> None:
        self.counter = LineCounter()
        self.reported = 0

    async def __call__(self, event: dict[str, object]) -> None:
        delta = event.get("delta")
        if isinstance(delta, dict):
            content = delta.get("content")
            if not isinstance(content, str) or not content:
                return
            lines = self.counter.feed(content)
            if (
                self.counter.inside
                and lines >= 2
                and lines > self.reported
                and time.monotonic() - self.last >= PROGRESS_SECONDS
            ):
                self.reported = lines
                await self.status(1, lines)
            return
        if event.get("phase") in {"provider_fallback", "reasoning_fallback"}:
            # The gateway restarts the call before any content: so does the count.
            self.restart()
            await self.status(0)


@dataclass
class Candidate:
    """A file that works, delivered unless its repair gives a better one."""

    document: str
    title: str
    summary: str
    # The written block (atlas-media references or stand-ins), for the media note.
    content: str
    soft: list[str] = field(default_factory=list)
    losses: list[app_features.Feature] = field(default_factory=list)
    # The checks of the expectations ledger on this file (expectations.py).
    results: list[expectations.Result] = field(default_factory=list)
    # Its render check, kept for the follow-up that changes it.
    report: app_render.RenderReport | None = None

    def weight(self) -> int:
        return (
            len(self.soft) + len(self.losses) + len(expectations.failed(self.results))
        )


def current_note(previous: Current, report: app_render.RenderReport) -> str:
    """What the render check saw in the version a follow-up changes."""
    return CURRENT_CHECK.format(
        version=previous.version,
        seconds=app_render.LATER_MS // 1000,
        found=CURRENT_PROBLEMS.format(problems=app_render.repair_lines(report))
        if report.problems
        else CURRENT_FINE,
    )


def _remember_current(
    task: asyncio.Task[app_render.RenderReport],
    current: Current,
    item: Interaction,
    seen: list[app_render.RenderReport],
) -> None:
    """Keep the report of the current version's check run beside the model."""
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        logging.getLogger(__name__).warning(
            "app_turn_current_check_failed error=%s", type(error).__name__
        )
        return
    report = task.result()
    item.latence_ms["current_render_check"] = report.ms
    if report.status in {"ok", "problems"}:
        seen.append(report)
        app_render.remember_version(current.artifact_id, current.version, report)


def _follow_up(previous: Current | None) -> str:
    """What every repair of a follow-up adds: start from the stored version."""
    if previous is None or is_drawing(previous.source):
        return ""
    return FOLLOW_UP_REPAIR.format(version=previous.version)


def _also_lost(previous: Current | None, written: str, kind: Kind) -> str:
    """The losses of a rejected follow-up file, fixed in the same repair.

    Staging -142 replays: the first file of "it does not work" used web
    addresses and was rejected; its repair wrote the app again and dropped the
    sound, with no call left to restore it.
    """
    if previous is None or kind != "app":
        return ""
    losses = app_features.lost(previous.source, written)
    if not losses:
        return ""
    return "\n" + ALSO_LOST.format(
        version=previous.version, losses=app_features.repair_lines(losses)
    )


def _keeps(text: str) -> bool:
    """The model's answer to a loss repair is the one word that keeps the file."""
    return prose(text).strip().strip(".!*`'\"").upper() == KEEP_WORD


@dataclass
class Current:
    artifact_id: str
    version: int
    title: str
    filename: str
    source: str
    # The files embedded in the stored version (packages/artifact_media.py).
    media: list[Media] = field(default_factory=list)


def _current(current: CurrentArtifact | None) -> tuple[Current | None, bool]:
    """The artifact a follow-up changes, and whether a previous one was lost."""
    if current is None:
        return None, False
    artifact = current.artifact
    if not isinstance(artifact, dict):
        return None, True
    content = artifact.get("content")
    version = artifact.get("version")
    if not isinstance(content, str) or not isinstance(version, int):
        return None, True
    media = artifact.get("media")
    embedded = media if isinstance(media, list) else []
    return (
        Current(
            str(artifact.get("artifact_id")),
            version,
            str(artifact.get("title") or ""),
            str(artifact.get("filename") or ""),
            content,
            [item for item in embedded if isinstance(item, Media)],
        ),
        False,
    )


def _ledger(
    request: ChatRequest,
    previous: Current | None,
    kind: Kind,
    language: str,
    spend: Any,
) -> expectations.TurnLedger | None:
    """The expectations ledger of the app this turn writes (stored with each
    version of the artifact), or None for a drawing or when it is off."""
    if kind != "app" or not expectations.enabled():
        return None
    stored = (
        expectations.load(f"app:{previous.artifact_id}", previous.version)
        if previous is not None
        else None
    )
    return expectations.TurnLedger(
        f"app:{previous.artifact_id}" if previous is not None else "",
        stored[0] if stored else expectations.Ledger("app"),
        str(request.model),
        language,
        strict=privacy.owned_only(),
        spend=spend,
    )


def _history(request: ChatRequest) -> list[Message]:
    messages: list[Message] = []
    for message in request.messages[:-1][-HISTORY_MESSAGES:]:
        if message.role not in {"user", "assistant"} or not message.text.strip():
            continue
        text = message.text
        if len(text) > HISTORY_CHARS:
            text = text[:HISTORY_CHARS] + " […]"
        messages.append({"role": message.role, "content": text})
    return messages


def _summary(text: str) -> str:
    from services.orchestrator.artifact_tools import clean_summary

    summary = clean_summary(described(text))
    if len(summary) <= SUMMARY_CHARS:
        return summary
    cut = summary[:SUMMARY_CHARS]
    end = max(cut.rfind(". "), cut.rfind(".\n"), cut.rfind("! "), cut.rfind("? "))
    return (cut[: end + 1] if end > 0 else cut).strip()


def _problem_lines(problems: list[Problem], source: str) -> str:
    lines = source.splitlines()
    rendered = []
    for problem in problems[:MAX_PROBLEMS]:
        detail = PROBLEM_DETAILS.get(problem.code) or EXTRA_DETAILS.get(problem.code)
        if detail is None and 1 <= problem.line <= len(lines):
            detail = lines[problem.line - 1].strip()[:MAX_DETAIL_CHARS]
            if problem.code == "unknown_media":
                detail = (
                    "this atlas-media reference does not exist, use only the "
                    "references Atlas listed: " + detail
                )
        if problem.detail:
            # The checker's own finding (the parser's message and line).
            detail = f"{detail} ({problem.detail})" if detail else problem.detail
        rendered.append(f"- line {problem.line}: {problem.code}: {detail or ''}")
    return "\n".join(rendered)


async def process_app_turn(
    request: ChatRequest,
    item: Interaction,
    *,
    intent: str,
    kind: Kind,
    current: CurrentArtifact | None,
    cost_limit: Decimal | None = None,
    wall_clock_limit: float | None = None,
) -> None:
    """Write, check and store the app or drawing of this turn; answer with its link."""
    started = time.monotonic()
    language = request.lang if request.lang in LANGUAGES else "en"
    previous, lost = _current(current) if intent != "create" else (None, False)
    if previous is not None:
        kind = "drawing" if is_drawing(previous.source) else "app"
    progress = Progress(sink_context.get(), kind, request.status_language)
    item.task_type = "artifact"
    deadline = started + (
        request.timeout_seconds if wall_clock_limit is None else wall_clock_limit
    )
    fence = "svg" if kind == "drawing" else "html"
    failure = "artifact_missing"
    summary = ""
    redescribe = False
    timed_out = False
    model: GatewayModel | None = None
    # The plan of an app (work_plan.py): write, check, hand over. The code
    # defines it, since one call writes the whole app; each step is checked
    # by the code itself (block received, validation and render check passed,
    # app stored).
    plan = _app_plan(request, previous is not None) if kind == "app" else None

    def spend(amount: Decimal) -> None:
        if model is not None:
            model.spent += amount

    # What the person asked of this app across the conversation
    # (expectations.py): updated from this message, checked on every file.
    expect = _ledger(request, previous, kind, language, spend)
    delivered_results: list[expectations.Result] = []
    built = False
    render_codes: list[str] = []
    # The best file written so far that works (render check passed, or only
    # soft defects), kept while a repair is tried.
    pending: Candidate | None = None
    keep_offered = False
    store_failed = False
    fetched: list[Media] = []
    failed: list[app_media.MediaFailure] = []

    # The current version's render report (remembered, or from its check beside
    # the model call), said to the model once.
    current_seen: list[app_render.RenderReport] = []
    last_call_seconds = 0.0
    current_told: list[bool] = []

    def told_current() -> str:
        if previous is None or not current_seen or current_told:
            return ""
        current_told.append(True)
        return "\n" + current_note(previous, current_seen[0])

    def deliver(candidate: Candidate) -> str:
        """Store the file and answer with its link; the store error code, or ""."""
        nonlocal built, delivered_results
        if previous is not None and unchanged(previous, candidate):
            # C2 (class suite, staging -184): every change claimed must be true
            # of the stored app. Code identical to the current version is not
            # stored again, and the model's summary, which describes a change,
            # is replaced by the plain statement that nothing was changed.
            delivered_results = candidate.results
            if plan is not None:
                plan.step_note(1, NOTHING_CHANGED.get(language, NOTHING_CHANGED["en"]))
            item.artifacts = []
            item.reponse = compose_answer(
                "",
                identical_answer(
                    _held_to_facts(
                        candidate.summary,
                        app_facts.measure(candidate.document),
                        language,
                    )
                    if kind == "app"
                    else candidate.summary,
                    previous.version,
                    language,
                ),
                previous.artifact_id,
                previous.version,
                previous.title or candidate.title,
                previous.filename or filename_for(candidate.title, kind),
                language,
                temporary=False,
                note=expectations.note(language, expect.ledger, candidate.results)
                if expect is not None
                else "",
            )
            item.state = "done"
            item.latence_ms["artifact_ready"] = (time.monotonic() - started) * 1000
            built = True
            return ""
        stored = _store(
            request, item, candidate.document, candidate.title, kind, previous
        )
        if isinstance(stored, str):
            return stored
        artifact_id, version, filename = stored
        delivered_results = candidate.results
        if expect is not None:
            expect.settle(candidate.results)
            expectations.save(f"app:{artifact_id}", version, expect.ledger)
            if plan is not None:
                plan.step_note(
                    2, expectations.status_label(plan.language, candidate.results)
                )
        if candidate.report is not None:
            app_render.remember_version(artifact_id, version, candidate.report)
        # The addresses the note lists are the code's own downloads: the
        # citation guard keeps them (it wrote "[unverified source]" in their
        # place, class C4, staging -145).
        item.tool_urls.extend(
            url
            for url in dict.fromkeys(
                [media.url for media in fetched] + [failure.url for failure in failed]
            )
            if url and url not in item.tool_urls
        )
        notes = [
            # Which of the person's files the app embeds, and which not.
            _media_answer(language, fetched, failed, candidate.content),
            app_soft_defects(language, candidate.soft),
            app_lost_features(
                language,
                previous.version,
                [(feature.kind, feature.shown()) for feature in candidate.losses],
            )
            if previous is not None and candidate.losses
            else "",
            # Requirements still unmet or unchecked, by name (never claimed).
            expectations.note(language, expect.ledger, candidate.results)
            if expect is not None
            else "",
        ]
        summary = (
            _held_to_facts(
                candidate.summary,
                app_facts.measure(candidate.document),
                language,
            )
            if kind == "app"
            else candidate.summary
        )
        item.reponse = compose_answer(
            "",
            summary,
            artifact_id,
            version,
            candidate.title,
            filename,
            language,
            # A drawing has no data to lose on reload.
            temporary=kind == "app",
            note="\n\n".join(note for note in notes if note),
        )
        item.state = "done"
        item.latence_ms["artifact_ready"] = (time.monotonic() - started) * 1000
        built = True
        return ""

    await progress.status(0)
    try:
        model = await GatewayModel.connect(
            os.environ.get("ATLAS_GATEWAY_URL", atlas_ports().url("gateway")),
            local_enabled=os.environ.get("GPU_LOCAL", "0") == "1",
        )
        model.tools = []
        model.observing = True
        model.local_enabled = os.environ.get("GPU_LOCAL", "0") == "1"
        model.configure_quality(request.model, output_tokens(), produces_files=True)
        # Reasoning would spend the output room before the file (and is not
        # streamed as progress); the file itself is the work.
        model.reasoning_ceiling = "none"
        model.cost_ceiling = cost_limit
        model.sink = progress
        messages: list[Message] = [
            {
                "role": "system",
                "content": instructions(kind)
                + "\n"
                + answer_instruction(request.prose_lang),
            },
            *_history(request),
        ]
        if previous is not None:
            source = (
                drawing_source(previous.source)
                if kind == "drawing"
                else previous.source
            )
            messages.append(
                {
                    "role": "system",
                    "content": CURRENT_SOURCE[kind].format(
                        version=previous.version, source=source
                    ),
                }
            )
            if kind == "app":
                # What the stored file is, measured: an explanation of the
                # app rests on it (staging -178: "embedded only inside Atlas").
                messages.append(
                    {
                        "role": "system",
                        "content": app_facts.model_note(
                            _stored_facts(previous), previous.version
                        ),
                    }
                )
        elif lost:
            noun = "drawing" if kind == "drawing" else "app"
            messages.append(
                {"role": "system", "content": PREVIOUS_UNAVAILABLE.format(noun=noun)}
            )
        # This message's requirements are extracted while the files are
        # fetched and the current app is opened (expectations.py).
        extraction = (
            asyncio.create_task(
                expect.update(
                    request.messages[-1].text,
                    [
                        message.text
                        for message in request.messages[:-1]
                        if message.role == "user" and message.text.strip()
                    ],
                    expectations.turn_number(request.messages),
                    deadline
                    - time.monotonic()
                    - MINIMUM_CALL_SECONDS
                    - DEADLINE_MARGIN
                    - 30,
                )
            )
            if expect is not None
            else None
        )
        # What the check saw in the current version. Its report from the
        # turn that stored it is given at once; otherwise the version is
        # opened beside the model call (never before it: staging -145) and
        # what it saw goes with a repair.
        if previous is not None and kind == "app" and app_render.mode() != "off":
            remembered = app_render.version_report(
                previous.artifact_id, previous.version
            )
            if remembered is not None:
                current_seen.append(remembered)
                current_told.append(True)
                item.latence_ms["current_render_check"] = 0.0
                messages.append(
                    {"role": "system", "content": current_note(previous, remembered)}
                )
            else:
                baseline = asyncio.create_task(
                    app_render.check(
                        artifact_media.embed(previous.source, previous.media),
                        # Its colours: what a requested colour change starts from.
                        palette=expect is not None,
                    )
                )
                baseline.add_done_callback(
                    functools.partial(
                        _remember_current,
                        current=previous,
                        item=item,
                        seen=current_seen,
                    )
                )
        try:
            media, fetched, failed = await _media(
                request, kind, previous, deadline - time.monotonic()
            )
            if extraction is not None:
                extracted = time.monotonic()
                await extraction
                item.latence_ms["expectations_update"] = (
                    time.monotonic() - extracted
                ) * 1000
        except BaseException:
            if extraction is not None:
                extraction.cancel()
            raise
        if media:
            # New downloads, and on a follow-up the files the app embeds already.
            messages.append(
                {
                    "role": "system",
                    "content": MEDIA_FILES.format(
                        lines="\n".join(_media_line(item) for item in media)
                    ),
                }
            )
        if failed:
            messages.append(
                {
                    "role": "system",
                    "content": MEDIA_FAILED.format(
                        urls=", ".join(failure.url for failure in failed)
                    ),
                }
            )
        if expect is not None and expect.brief():
            # The expected result, stated before the file is written.
            messages.append({"role": "system", "content": expect.brief()})
        messages.append({"role": "user", "content": request.messages[-1].text})
        # Unmet requirements may take one more repair (expectations.MAX_REPAIRS
        # in all), within the turn's time.
        calls = MAX_CALLS + (1 if expect is not None and expect.checks else 0)
        retries = Retries()
        for attempt in range(calls):
            remaining = deadline - time.monotonic() - DEADLINE_MARGIN
            if remaining < MINIMUM_CALL_SECONDS:
                if attempt == 0:
                    failure = "timeout"
                timed_out = failure == "timeout"
                break
            if attempt:
                progress.restart()
                repair = failure in {
                    "artifact_invalid",
                    "artifact_missing",
                    "artifact_render",
                    "artifact_lost",
                    "artifact_unmet",
                }
                await progress.status(3 if repair else 0)
            call_started = time.monotonic()
            try:
                turn = await _complete(
                    model, messages, deadline, progress, item, retries
                )
                last_call_seconds = time.monotonic() - call_started
            except TimeoutError:
                failure, timed_out = "timeout", True
                break
            except GatewayError as exc:
                if exc.code in BUDGET_CODES:
                    raise
                failure = exc.code
                logging.getLogger(__name__).warning(
                    "app_turn_call_failed code=%s attempt=%d", exc.code, attempt + 1
                )
                continue
            except LimitError as exc:
                if exc.code in BUDGET_CODES:
                    raise
                failure = exc.code
                continue
            except (RuntimeError, ValueError, ValidationError) as exc:
                failure = (
                    str(exc)
                    if str(exc).isidentifier() and len(str(exc)) <= 60
                    else type(exc).__name__
                )
                logging.getLogger(__name__).warning(
                    "app_turn_call_failed code=%s attempt=%d", failure, attempt + 1
                )
                continue
            text = turn.text
            block = last_block(text)
            if block is not None and (not summary or redescribe):
                # After a repair the model may describe its fix: the person
                # wants the description of the app it first wrote, unless the
                # repair took out what the first file loaded from the network
                # (DESCRIBE).
                summary = _summary(text) or summary
                redescribe = False
            if block is None and pending is not None:
                # The repair of a working file that lost features or kept a
                # defect: KEEP when the request asked for the removal, any
                # other answer keeps the file with what it lacks said.
                if keep_offered and _keeps(text):
                    pending = dataclasses.replace(pending, losses=[])
                failure = deliver(pending)
                if not failure:
                    return
                store_failed = True
                break
            if block is None:
                answer = prose(text)
                question = answer.rstrip().endswith("?") and len(answer) <= 800
                if answer and (previous is not None or question):
                    # A question about the app, or the one essential question.
                    if previous is not None and kind == "app":
                        answer = _held_to_facts(
                            answer, _stored_facts(previous), language
                        )
                    item.reponse, item.state = answer, "done"
                    if plan is not None:
                        plan.discard()
                    return
                failure = "artifact_missing"
                messages += [
                    {"role": "assistant", "content": text or "(empty)"},
                    {"role": "user", "content": NO_BLOCK_REPAIR.format(fence=fence)},
                ]
                continue
            await progress.status(2)
            if plan is not None:
                plan.code_step(2)
            title = (
                title_of(block.content)
                or (previous.title if previous is not None else "")
                or DEFAULT_TITLES[kind].get(language, DEFAULT_TITLES[kind]["en"])
            )
            # What the model wrote, with its atlas-media references: compared
            # with the current version, which the model read the same way.
            written = block.content
            if media:
                # The static checks read the code: references become short
                # stand-ins, the bytes are embedded once the file passes.
                block = dataclasses.replace(
                    block, content=artifact_media.stand_in(block.content, media)
                )
            prepared = prepare(block, title, language)
            missing = artifact_media.unknown(block.content, media)
            if missing:
                lines = block.content.splitlines()
                prepared = dataclasses.replace(
                    prepared,
                    problems=prepared.problems
                    + [
                        Problem(
                            "unknown_media",
                            next(
                                (
                                    index + 1
                                    for index, line in enumerate(lines)
                                    if "atlas-media:" + name in line
                                ),
                                1,
                            ),
                        )
                        for name in missing
                    ],
                )
            if prepared.problems:
                failure = "artifact_invalid"
                network = any(
                    problem.code in NETWORK_PROBLEMS for problem in prepared.problems
                )
                redescribe = network
                logging.getLogger(__name__).warning(
                    "app_turn_invalid attempt=%d codes=%s",
                    attempt + 1,
                    ",".join(sorted({problem.code for problem in prepared.problems})),
                )
                messages += [
                    {"role": "assistant", "content": text},
                    {
                        "role": "user",
                        "content": INVALID_REPAIR.format(
                            problems=_problem_lines(prepared.problems, prepared.source),
                            network=NETWORK_REPAIR if network else "",
                            fence="svg" if block.kind == "svg" else "html",
                        )
                        + _follow_up(previous)
                        + _also_lost(previous, written, kind)
                        + told_current(),
                    },
                ]
                continue
            document = artifact_media.embed(prepared.document, media)
            if kind == "app":
                # Open the app as the side view would before saying it is done
                # (owner rule 2026-11-16, Atlas checks its own result).
                report = await app_render.check(
                    document,
                    min(
                        deadline - time.monotonic() - DEADLINE_MARGIN,
                        CHECK_BUDGET_SECONDS
                        - item.latence_ms.get("render_check", 0.0) / 1000,
                    ),
                    assertions=expectations.assertions(expect.ledger)
                    if expect is not None
                    else None,
                    palette=expect is not None,
                )
                item.latence_ms["render_check"] = (
                    item.latence_ms.get("render_check", 0.0) + report.ms
                )
                if report.status == "skipped":
                    logging.getLogger(__name__).warning(
                        "app_turn_render_skipped attempt=%d reason=%s",
                        attempt + 1,
                        report.reason,
                    )
                soft: list[str] = []
                if report.status == "problems":
                    render_codes = report.codes
                    network = any(
                        code in app_render.NETWORK_CODES for code in report.codes
                    )
                    logging.getLogger(__name__).warning(
                        "app_turn_render attempt=%d codes=%s",
                        attempt + 1,
                        ",".join(report.codes),
                    )
                    if app_render.hard(report):
                        failure = "artifact_render"
                        redescribe = network
                        messages += [
                            {"role": "assistant", "content": text},
                            {
                                "role": "user",
                                "content": RENDER_REPAIR.format(
                                    problems=app_render.repair_lines(report),
                                    network=NETWORK_REPAIR if network else "",
                                )
                                + _follow_up(previous)
                                + _also_lost(previous, written, kind)
                                + told_current(),
                            },
                        ]
                        continue
                    soft = report.codes
                # A follow-up keeps every feature of the version it changes
                # unless the request removes it (app_features.py).
                losses = (
                    app_features.lost(previous.source, written)
                    if previous is not None
                    else []
                )
                if losses:
                    logging.getLogger(__name__).warning(
                        "app_turn_lost attempt=%d kinds=%s",
                        attempt + 1,
                        ",".join(sorted({feature.kind for feature in losses})),
                    )
                # Every requirement the person stated, checked on this file.
                results: list[expectations.Result] = []
                if expect is not None and expect.checks:
                    if current_seen:
                        # The current version's colours (its kept report).
                        expect.capture_baseline(current_seen[0].palette)
                    results = await expect.check_app(
                        written,
                        report.assertions
                        if report.status in {"ok", "problems"}
                        else None,
                        report.palette,
                        deadline - time.monotonic() - DEADLINE_MARGIN,
                    )
                unmet = expectations.failed(results)
                repair_unmet = (
                    bool(unmet)
                    and expect is not None
                    and expect.repairs < expectations.MAX_REPAIRS
                )
                if unmet:
                    logging.getLogger(__name__).warning(
                        "app_turn_unmet attempt=%d ids=%s",
                        attempt + 1,
                        ",".join(result.id for result in unmet),
                    )
                candidate = Candidate(
                    document,
                    title,
                    summary,
                    block.content,
                    soft,
                    losses,
                    results=results,
                    report=report,
                )
                if pending is None or candidate.weight() <= pending.weight():
                    pending = candidate
                # The file works: a repair for a soft defect, a loss or an
                # unmet requirement is made only when a second write still
                # fits an ordinary turn (staging -145); otherwise the answer
                # says what remains.
                optional = (
                    time.monotonic() - started + last_call_seconds
                    <= OPTIONAL_REPAIR_SECONDS
                )
                if not optional and (soft or losses or repair_unmet):
                    logging.getLogger(__name__).warning(
                        "app_turn_repair_skipped attempt=%d seconds=%d",
                        attempt + 1,
                        time.monotonic() - started,
                    )
                if (
                    (soft or losses or repair_unmet)
                    and optional
                    and attempt + 1 < calls
                ):
                    failure = (
                        "artifact_render"
                        if soft
                        else "artifact_lost"
                        if losses
                        else "artifact_unmet"
                    )
                    keep_offered = bool(losses) and not soft and not unmet
                    repair_text = (
                        RENDER_REPAIR.format(
                            problems=app_render.repair_lines(report), network=""
                        )
                        if soft
                        else ""
                    )
                    if repair_unmet and expect is not None:
                        expect.repairs += 1
                        repair_text = "\n".join(
                            filter(
                                None,
                                (
                                    repair_text,
                                    UNMET_REPAIR.format(
                                        lines=expect.repair_lines(results)
                                    ),
                                ),
                            )
                        )
                    if losses and previous is not None:
                        repair_text = "\n".join(
                            filter(
                                None,
                                (
                                    repair_text,
                                    LOSS_REPAIR.format(
                                        version=previous.version,
                                        losses=app_features.repair_lines(losses),
                                    )
                                    + (LOSS_KEEP if keep_offered else ""),
                                ),
                            )
                        )
                    messages += [
                        {"role": "assistant", "content": text},
                        {
                            "role": "user",
                            "content": repair_text
                            + ("" if losses else _follow_up(previous))
                            + told_current(),
                        },
                    ]
                    continue
            else:
                pending = Candidate(document, title, summary, block.content)
            failure = deliver(pending)
            if not failure:
                return
            store_failed = True
            break
        if pending is not None and not store_failed:
            # The repair did not give a better file (no time, failed call,
            # broken file): the working file is delivered with what it lacks.
            failure = deliver(pending)
            if not failure:
                return
        item.erreurs.append(failure)
        item.reponse = (
            # D5: a storage quota is said with its limits, never as a failure
            # that a retry would fix.
            app_quota(language, *artifact_store.quota_limits())
            if failure == "artifact_quota"
            # The app was written but did not work when opened: said as such.
            else app_render_failure(
                language,
                render_codes,
                unchanged_version=previous.version if previous is not None else None,
            )
            if failure == "artifact_render"
            else app_failure(
                kind,
                language,
                timeout=timed_out,
                unchanged_version=previous.version if previous is not None else None,
                cause=failure_cause(failure),
            )
        )
        item.state = "done"
    except (GatewayError, LimitError) as exc:
        if exc.code in BUDGET_CODES:
            raise
        _fail(item, exc.code, kind, language, previous, timed_out=False)
    except TimeoutError:
        _fail(item, "timeout", kind, language, previous, timed_out=True)
    except (
        RuntimeError,
        ValueError,
        OSError,
        aiohttp.ClientError,
        ValidationError,
    ) as exc:
        logging.getLogger(__name__).warning(
            "app_turn_failed error=%s", type(exc).__name__
        )
        _fail(item, "provider_error", kind, language, previous, timed_out=False)
    finally:
        if expect is not None:
            if not built and previous is not None and expect.updated:
                # No new version: what this message asked is kept for the next.
                expectations.save(expect.key, previous.version, expect.ledger)
            expect.log(delivered_results, built=built)
        if model is not None:
            _record(item, model)
        if expect is not None and expect.failed_calls:
            item.steps.append(
                {
                    "phase": "expectations",
                    "checks_skipped": len(expect.failed_calls),
                }
            )
        if plan is not None:
            plan.finish(built, item.cout_eur)
        item.latence_ms["first_progress"] = (progress.first_status or 0.0) * 1000
        if progress.first_lines is not None:
            item.latence_ms["first_lines"] = progress.first_lines * 1000


async def _media(
    request: ChatRequest, kind: Kind, previous: Current | None, seconds: float
) -> tuple[list[Media], list[Media], list[app_media.MediaFailure]]:
    """The files the app may use: those it embeds already and those the
    request names (downloaded now), then the downloads that failed."""
    carried = list(previous.media) if previous is not None else []
    if kind != "app":
        return carried, [], []
    text = request.messages[-1].text
    if not app_media.media_urls(text):
        return carried, [], []
    # Downloads leave most of the turn to writing the app.
    budget = min(30.0, seconds - MINIMUM_CALL_SECONDS - DEADLINE_MARGIN - 30.0)
    if budget < 2:
        return carried, [], []
    fetched, failed = await app_media.fetch_media(
        text,
        budget,
        {item.name for item in carried},
        {item.url for item in carried if item.url},
    )
    return carried + fetched, fetched, failed


def _media_answer(
    language: str,
    fetched: list[Media],
    failed: list[app_media.MediaFailure],
    content: str,
) -> str:
    """The files the app embeds, then those not embedded: failed downloads
    and downloads the app does not use."""
    used = [
        item
        for item in fetched
        if item.reference in content or item.stand_in() in content
    ]
    unused = [
        app_media.MediaFailure(item.url, "unused")
        for item in fetched
        if item not in used
    ]
    return app_media.media_note(language, used, failed + unused)


def _stored_facts(previous: Current) -> app_facts.FileFacts:
    """The facts of the stored version: its files embedded, as downloaded."""
    return app_facts.measure(
        artifact_media.embed(previous.source, previous.media),
        [item.filename or item.name for item in previous.media],
    )


def _held_to_facts(text: str, facts: app_facts.FileFacts, language: str) -> str:
    """The answer without what it says of the file against the facts."""
    checked, kinds = app_facts.checked_answer(text, facts, language)
    if kinds:
        logging.getLogger(__name__).warning(
            "app_turn_claims_removed kinds=%s", ",".join(sorted(set(kinds)))
        )
    # Whether the app was tried out is the code's to state (the render check
    # and the requirements' results): a sentence that says it was tested, or
    # gives results as observed, is the writer's guess.
    from services.orchestrator import file_claims

    return file_claims.without_unproven_status(checked) or checked


def _media_line(item: Media) -> str:
    kind = item.mime.split("/", 1)[0]
    size = f"{max(1, len(item.data) // 1000)} KB"
    origin = item.filename or item.url or "already in the app"
    if item.converted_from:
        origin += ", drawn by Atlas as a PNG picture from the SVG file"
    return f"- {item.reference}: {kind} ({item.mime}, {size}), {origin}"


def _app_plan(request: ChatRequest, change: bool) -> ChatPlan | None:
    """Show the plan of this app turn, its first step in progress."""
    from services.orchestrator import work_estimates, work_plan

    plan = work_plan.current.get()
    if plan is None or plan.shown or plan.finished:
        return None
    plan.route = "app"
    plan.propose(work_plan.app_steps(plan.language, change), source="app")
    plan.classify(
        work_estimates.remaining_month(
            getattr(request, "daily_ledger", None),
            str(getattr(request, "daily_user", "") or ""),
        )
    )
    plan.code_step(1)
    return plan


def _fail(
    item: Interaction,
    code: str,
    kind: Kind,
    language: str,
    previous: Current | None,
    *,
    timed_out: bool,
) -> None:
    item.erreurs.append(code)
    item.reponse = app_failure(
        kind,
        language,
        timeout=timed_out,
        unchanged_version=previous.version if previous is not None else None,
        cause=failure_cause(code),
    )
    item.state = "done"


@dataclass
class Retries:
    """The transient retries a turn has used (at most len(TRANSIENT_WAITS))."""

    used: int = 0


def failure_cause(code: str) -> Literal["checks", "provider"] | None:
    """The cause the failure sentence names: files that never passed the checks,
    or a model service that kept failing (user_errors.app_failure)."""
    if code == "artifact_invalid":
        return "checks"
    if code in TRANSIENT_FAILURES:
        return "provider"
    return None


async def _complete(
    model: GatewayModel,
    messages: list[Message],
    deadline: float,
    progress: Progress,
    item: Interaction,
    retries: Retries,
) -> Turn:
    """One write of the file. A transient provider failure is tried again
    (TRANSIENT_WAITS, counted for the whole turn) while a call still fits the
    turn; any other failure, or the last one, is raised to the caller."""
    while True:
        # The gateway accepts at most 300 s for a file turn.
        remaining = min(deadline - time.monotonic() - DEADLINE_MARGIN, 290.0)
        try:
            async with asyncio.timeout(remaining):
                return await model.complete(messages, remaining)
        except (GatewayError, RuntimeError, ValueError) as exc:
            code = exc.code if isinstance(exc, GatewayError) else str(exc)
            if isinstance(exc, GatewayError):
                item.provider_retries += exc.retries
            if code not in TRANSIENT_FAILURES or retries.used >= len(TRANSIENT_WAITS):
                raise
            wait = TRANSIENT_WAITS[retries.used]
            if deadline - time.monotonic() - DEADLINE_MARGIN - wait < (
                MINIMUM_CALL_SECONDS
            ):
                raise
            retries.used += 1
            item.provider_retries += 1
            logging.getLogger(__name__).warning(
                "app_turn_call_retry code=%s retry=%d wait=%.0f",
                code,
                retries.used,
                wait,
            )
            await asyncio.sleep(wait)
            progress.restart()


# Next to the plan's "change the app" step when the code came back identical.
NOTHING_CHANGED = {
    "en": "nothing changed",
    "fr": "rien de modifié",
    "de": "nichts geändert",
    "es": "nada cambiado",
    "it": "nulla modificato",
}


def unchanged(previous: Current, candidate: Candidate) -> bool:
    """The candidate is the stored version again: same code and same title."""
    return candidate.document.strip() == previous.source.strip() and (
        not previous.title or candidate.title == previous.title
    )


def _store(
    request: ChatRequest,
    item: Interaction,
    document: str,
    title: str,
    kind: Kind,
    previous: Current | None,
) -> tuple[str, int, str] | str:
    """The stored artifact id, version and filename, or the store error code."""
    owner = getattr(request, "artifact_owner", "")
    content = document.encode("utf-8")
    try:
        if previous is not None:
            filename = previous.filename or filename_for(title, kind)
            result = artifact_store.update(
                previous.artifact_id,
                owner,
                title,
                filename,
                content,
                item.request_id,
                previous.version,
            )
        else:
            filename = filename_for(title, kind)
            result = artifact_store.create(
                owner, title, filename, content, item.request_id
            )
    except ArtifactError as error:
        logging.getLogger(__name__).warning("app_turn_store_failed code=%s", error.code)
        return error.code
    artifact_id, version = str(result["id"]), int(result["version"])
    stored = artifact_store.read(artifact_id, owner, version)
    item.artifacts = [
        {
            "id": artifact_id,
            "version": version,
            "bytes": stored.size_bytes,
            "sha256": stored.sha256,
        }
    ]
    return artifact_id, version, filename


def _record(item: Interaction, model: GatewayModel) -> None:
    item.provider_retries += model.provider_retries
    item.tokens = {"in": model.input_tokens, "out": model.output_tokens}
    item.provider_model = model.provider_model
    item.reasoning = model.reasoning
    item.trace_tokens, item.answer_tokens = model.trace_tokens, model.answer_tokens
    item.token_split_estimated = model.token_split_estimated
    item.reasoning_retried = model.reasoning_retried
    item.reasoning_effort = model.reasoning_used
    item.latence_ms["generation"] = model.generation_ms
    item.cout_eur = float(model.spent)
    if model.observations:
        item.modele_utilise = model.observations[-1].provider
        item.route_decision = model.observations[0].route
