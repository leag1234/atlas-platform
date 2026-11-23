"""A change to a chosen picture: the painter edits its own drawing, code finishes it.

"Make the lighthouse taller" after a picture answer goes through the picture
chain, never the conversation model (measured on staging: that model redrew
the painted picture as a crude pictogram). One painter-role call gets the
measured painter prompt of the first request, the painter's own SVG of the
current version (before the kits, with its pose and animal declarations) as
its previous answer, and the change; the same code steps then run (sanitise,
kits, labels, faces, contour) and the result is the next version of the same
artifact. No new set of four.

When the stored drawing is too large to give back to the painter, the
painter draws the picture again from the first request with the change
(said in the answer); the conversation model is never used for it.
"""

from __future__ import annotations

import asyncio
import logging
import time
from decimal import Decimal
from typing import Any

from packages.profiles import picture_profile
from services.orchestrator.pictures import (
    animal_kit_enabled,
    bounded_contours_enabled,
    cpu_checks,
    painter,
    store,
)
from services.orchestrator.pictures.svg_text import extract_svg, salvage
from services.orchestrator.pictures.texts import answer_language, text
from services.orchestrator.pictures.turn import (
    REFUSALS,
    Complete,
    Spend,
    _budget,
    _code,
    _gateway,
    _status,
    _store_image,
    profiles,
    turn_ceiling,
)
from services.orchestrator.pictures.worker import PictureJobError, run_job

LOGGER = logging.getLogger(__name__)
# Largest painter SVG given back to the painter for a change (bytes). A draft
# of 12 000 output tokens is about 40 kB; beyond this limit the input would
# not leave the painter room to write the changed picture again.
SOURCE_LIMIT = 60_000
CHANGE = (
    "Change the picture as asked: {change}\n"
    "Answer with the complete SVG of the changed picture. Keep everything the "
    "change does not touch exactly as it is: the composition, the style, the "
    "colours, the group ids, the head groups and their attributes, and the "
    '<desc class="pose"> and <desc class="animal"> declarations. When the '
    "change moves, enlarges or shrinks a person or an animal, update its "
    "declared joints accordingly."
)
REDRAW = "\nAlso apply this change: {change}"
# A change whose edit came back identical is drawn again from the request
# only when the turn still has this much time for one more painter call.
REDRAW_MIN_SECONDS = 60.0
# Drawings of one correction checked at most (the first and its retries).
CHECKED_DRAWINGS = 3
# Statements checked at most on one drawing.
MAX_STATEMENTS = 8
GAP_NOTE = (
    "\nA check of your last drawing found that it does not show: {gaps}. "
    "Make each of these plainly visible."
)


def statements_of(
    picture_set: store.PictureSet, requirements: list[str] | None, change: str
) -> list[str]:
    """What a correction's drawing is checked against: the picture's own
    statements, then what the person requires, once each; the change itself
    when neither says anything."""
    found: list[str] = []
    for statement in [*picture_set.checks, *(requirements or [])]:
        statement = " ".join(str(statement).split())
        if statement and statement.casefold() not in {f.casefold() for f in found}:
            found.append(statement)
    if not found and change.strip():
        found.append(" ".join(change.split())[:200])
    return found[:MAX_STATEMENTS]


async def _verified(
    statements: list[str],
    result: dict[str, Any],
    profile: str | None,
    spend: Any,
    complete: Any,
    errors: list[str],
) -> tuple[list[str] | None, dict[str, str]]:
    """(unmet, gaps) of one finished drawing; (None, {}) when unchecked."""
    image = result.get("image")
    if not statements or profile is None or not isinstance(image, dict):
        return None, {}
    data = image.get("data")
    if image.get("mime") != "image/png" or not isinstance(data, str):
        return None, {}
    return await cpu_checks.check(
        statements, data, profile, spend, complete or _gateway, errors
    )


def messages(
    picture_set: store.PictureSet, change: str
) -> tuple[list[dict[str, str]], bool]:
    """(messages of the change, whether the picture is drawn again)."""
    first = painter.messages(
        picture_set.request, picture_set.checklist, animal_kit=animal_kit_enabled()
    )
    source = picture_set.current_source
    if source and len(source.encode("utf-8")) <= SOURCE_LIMIT:
        return (
            [
                *first,
                {"role": "assistant", "content": source},
                {"role": "user", "content": CHANGE.format(change=change)},
            ],
            False,
        )
    # Too large (or no stored drawing): the painter draws it again from the
    # first request with the change; never the conversation model.
    return redraw_messages(picture_set, change), True


def redraw_messages(picture_set: store.PictureSet, change: str) -> list[dict[str, str]]:
    """The painter's first request with the change: the picture drawn again."""
    first = painter.messages(
        picture_set.request, picture_set.checklist, animal_kit=animal_kit_enabled()
    )
    first[1] = {
        "role": "user",
        "content": first[1]["content"] + REDRAW.format(change=change),
    }
    return first


async def process_picture_followup(
    request: Any,
    item: Any,
    current: Any,
    *,
    cost_limit: Decimal | None = None,
    wall_clock_limit: float | None = None,
    complete: Complete | None = None,
    instruction: str | None = None,
    requirements: list[str] | None = None,
) -> bool:
    """Change the conversation's picture; False when the artifact is not one.

    `instruction` is the change understood with its conversation
    (dialogue.py), the user's own words when not given. A chat that still
    shows an older version of the picture (a reply regenerated, two turns
    crossing) changes the latest version: the set always holds it, and the
    picture is never handed to the HTML artifact editor for a conflict."""
    from packages.artifact_source import Block, prepare
    from packages.user_errors import app_failure
    from services.orchestrator import artifact_store
    from services.orchestrator.artifact_store import ArtifactError
    from services.orchestrator.artifact_tools import footer

    artifact = getattr(current, "artifact", None)
    if not isinstance(artifact, dict) or not artifact.get("artifact_id"):
        return False
    picture_set = await asyncio.to_thread(
        store.by_artifact, str(artifact["artifact_id"])
    )
    owner = str(getattr(request, "artifact_owner", "") or "")
    if (
        picture_set is None
        or picture_set.owner != store.owner_hash(owner)
        or int(artifact.get("version") or 0) > picture_set.artifact_version
        or not picture_set.request
    ):
        # Not a picture by code, or a version this set does not know.
        return False
    if picture_set.engine == "cpu" and not (
        picture_set.by_code and picture_set.current_source
    ):
        from services.orchestrator.pictures import cpu_turn

        return await cpu_turn.change(
            request,
            item,
            picture_set,
            cost_limit=cost_limit,
            wall_clock_limit=wall_clock_limit,
            complete=complete,
            instruction=instruction,
            requirements=requirements,
        )
    chosen = profiles()
    if chosen is None:
        return False
    _, painter_profile = chosen
    started = time.monotonic()
    # A change keeps the language of the picture it changes.
    language = answer_language(picture_set.language, request.lang)
    item.task_type = "drawing"
    own = turn_ceiling()
    spend = Spend(min(own, cost_limit) if cost_limit is not None else own)
    limit = request.timeout_seconds if wall_clock_limit is None else wall_clock_limit
    seconds = max(30.0, min(240.0, limit - 40.0))
    wanted = (instruction or "").strip() or request.user_text
    change, redrawn = messages(picture_set, wanted)
    unchanged = app_failure(
        "drawing", language, unchanged_version=picture_set.artifact_version
    )
    await _status("changing", language)
    try:
        previous = artifact_store.read(
            picture_set.artifact_id, owner, picture_set.artifact_version
        )
    except ArtifactError as error:
        item.erreurs.append(error.code)
        item.reponse, item.state = unchanged, "done"
        return True
    method_changed = False
    # Verify before showing (docs/32): every drawing of a correction is
    # checked by the vision call against the picture's cumulative statements;
    # a miss is drawn again with the measured gap while the turn allows, and
    # the answer is never an unverified drawing presented as the correction.
    statements = statements_of(picture_set, requirements, wanted)
    check_profile = picture_profile("picture_rewrite")
    check_errors: list[str] = []
    tried: list[dict[str, Any]] = []
    while True:
        payload = {
            "messages": change,
            "tools": [],
            "profile": painter_profile,
            "max_tokens": painter.MAX_TOKENS,
            "produces_files": True,
            "timeout": min(seconds, 290.0),
            "budget_eur": _budget(spend.remaining()),
            "single_attempt": True,
            "local_enabled": False,
        }
        failure = ""
        try:
            reply = await (complete or _gateway)(payload, seconds + 10)
        except Exception as error:  # noqa: BLE001 - unchanged picture, or D5 refusal
            code = _code(error)
            if code in REFUSALS:
                # The whole allowance was offered to this one call: a cost or
                # quota refusal is the user's (D5), said with its measurements.
                raise
            failure = "picture_change_" + code
        if not failure:
            spend.settle(reply)
            item.cout_eur = float(spend.total)
            item.tokens = {"in": spend.tokens_in, "out": spend.tokens_out}
            svg, how = salvage(extract_svg(str(reply.get("text") or "")))
            if svg is None:
                failure = "picture_change_" + how.split(" ")[0]
        if not failure:
            try:
                result = await run_job(
                    "draft",
                    {
                        "svg": svg,
                        "checklist": picture_set.checklist,
                        "living": picture_set.living,
                        "animal_kit": animal_kit_enabled(),
                        "bounded_contours": bounded_contours_enabled(),
                        "labels_group": painter.labels_group(picture_set.checklist),
                    },
                )
            except PictureJobError as error:
                failure = "picture_change_" + str(error)
        if not failure:
            prepared = prepare(
                Block("svg", result["svg"], True),
                picture_set.title,
                picture_set.language,
            )
            if prepared.problems:
                failure = "picture_change_invalid"
        if failure:
            item.erreurs.append(failure)
            if tried:
                break
            item.reponse, item.state = unchanged, "done"
            return True
        left = limit - (time.monotonic() - started) - 40.0
        if previous.content == prepared.document.encode("utf-8"):
            # The drawing came back identical: no new version, no claimed change.
            item.erreurs.append("picture_change_identical")
            if tried:
                break
            if redrawn or left < REDRAW_MIN_SECONDS:
                # Already drawn again from the request, or no time to: said
                # plainly, with what was tried, never a request to say again
                # the correction the person already gave.
                item.reponse = text(
                    language,
                    "same_drawing_again" if method_changed else "same_drawing",
                    version=picture_set.artifact_version,
                )
                item.state = "done"
                return True
            # The painter gave its own drawing back unchanged to a correction
            # (staging -185): the method changes once, the picture is drawn
            # again from the first request with the correction.
            change, redrawn = redraw_messages(picture_set, wanted), True
            method_changed = True
            seconds = max(30.0, min(240.0, left))
            await _status("changing", language)
            continue
        unmet, gaps = await _verified(
            statements, result, check_profile, spend, complete, check_errors
        )
        tried.append(
            {
                "prepared": prepared,
                "result": result,
                "svg": svg,
                "unmet": unmet,
                "gaps": gaps,
            }
        )
        left = limit - (time.monotonic() - started) - 40.0
        if (
            unmet is None
            or not unmet
            or len(tried) >= CHECKED_DRAWINGS
            or left < REDRAW_MIN_SECONDS
        ):
            break
        # A miss: the correction again, with what the check measured.
        measured = GAP_NOTE.format(
            gaps="; ".join(
                statement
                + (f" (it shows: {gaps[statement]})" if gaps.get(statement) else "")
                for statement in unmet
            )
        )
        change, redrawn = (
            (redraw_messages(picture_set, wanted + measured), True)
            if redrawn
            else messages(picture_set, wanted + measured)
        )
        seconds = max(30.0, min(240.0, left))
        await _status("changing", language)
    item.erreurs.extend(check_errors)
    from services.orchestrator.pictures.cpu_turn import ranked

    best = ranked(tried)[0]
    prepared, result, svg = best["prepared"], best["result"], best["svg"]
    unmet, gaps = best["unmet"], best["gaps"]
    unsatisfied = isinstance(unmet, list) and bool(unmet)
    if unsatisfied and store.current_passed(picture_set):
        # Never a verified picture replaced by one that fails its check.
        item.erreurs.append("picture_change_kept")
        item.reponse = text(
            language,
            "code_kept",
            n=len(tried),
            items=cpu_checks.gapped(language, list(unmet or []), gaps),
            version=picture_set.artifact_version,
        )
        item.state = "done"
        return True
    try:
        stored = artifact_store.update(
            picture_set.artifact_id,
            owner,
            picture_set.title,
            previous.filename,
            prepared.document.encode("utf-8"),
            item.request_id,
            picture_set.artifact_version,
        )
    except ArtifactError as error:
        item.erreurs.append(error.code)
        item.reponse, item.state = unchanged, "done"
        return True
    image = await _store_image(result["image"])
    picture_set.artifact_version = int(stored["version"])
    picture_set.current_source = str(result.get("source") or svg)
    picture_set.image_key = image.rsplit("/", 1)[-1]
    picture_set.checks = list(statements)
    picture_set.unmet = None if unmet is None else list(unmet)
    picture_set.misses = picture_set.misses + 1 if unsatisfied else 0
    # A proposal is applied once (dialogue.follow_up): a new version ends it.
    picture_set.proposed = []
    await asyncio.to_thread(store.update, picture_set)
    if unsatisfied:
        item.erreurs.append("picture_change_unsatisfied")
        checked = text(
            language,
            "code_unsatisfied",
            n=len(tried),
            items=cpu_checks.gapped(language, list(unmet or []), gaps),
        )
    elif unmet is None:
        item.erreurs.append("picture_change_unchecked")
        checked = text(
            language, "code_check_unknown", items=cpu_checks.items(language, statements)
        )
    elif len(tried) > 1:
        checked = text(
            language,
            "code_retried",
            n=len(tried),
            items=cpu_checks.items(language, statements),
        )
    else:
        checked = ""
    lead = text(
        language,
        "redrawn_after_same"
        if method_changed
        else ("redrawn" if redrawn else "changed"),
    )
    lines = [" ".join(filter(None, [lead, checked]))]
    lines.append(f"![{picture_set.title}]({image})")
    lines.append(
        footer(
            picture_set.artifact_id,
            picture_set.artifact_version,
            picture_set.title,
            previous.filename,
            language,
            temporary=False,
        )
    )
    item.images = [{"reference": image, "kind": "picture"}]
    item.artifacts = [
        {"id": picture_set.artifact_id, "version": picture_set.artifact_version}
    ]
    item.reponse, item.state = "\n\n".join(lines), "done"
    item.latence_ms["picture_change"] = (time.monotonic() - started) * 1000
    LOGGER.info(
        "picture_change redrawn=%s cost_eur=%s seconds=%.1f",
        redrawn,
        spend.total,
        time.monotonic() - started,
    )
    return True
