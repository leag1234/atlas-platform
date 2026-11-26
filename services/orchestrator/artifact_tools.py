"""Current app of a conversation, and the answer footer that links to it.

The app or drawing itself is written by the model in its answer and stored by
services/orchestrator/app_turn.py; no tool carries it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.parse import quote, unquote

from services.orchestrator import artifact_store
from services.orchestrator.artifact_store import ArtifactError
from services.orchestrator.loop import Message
from packages import linear_scan


FOOTER_LABELS: dict[str, dict[str, str]] = {
    "en": {
        "open": 'Open "{title}" (version {version})',
        "download": "Download {filename}",
        "temporary": "Temporary preview: data entered in the app is not kept.",
        "unchanged": (
            "No new version was made on this turn; version {version} is still "
            "current. Ask me again for the change you want."
        ),
        "identical": (
            "Nothing was changed: the code written for this request is identical "
            "to version {version}, which stays the current one."
        ),
        "identical_ask": (
            "I wrote the same code again, so I found nothing to change. If what "
            "you see differs from what you expect, tell me the steps you took and "
            "what appears on the screen, and I will look at exactly that."
        ),
    },
    "fr": {
        "open": "Ouvrir « {title} » (version {version})",
        "download": "Télécharger {filename}",
        "temporary": (
            "Aperçu temporaire : les données saisies dans l'application "
            "ne sont pas conservées."
        ),
        "unchanged": (
            "Aucune nouvelle version n'a été créée à ce tour ; la version "
            "{version} reste actuelle. Demande-moi encore la modification "
            "souhaitée."
        ),
        "identical": (
            "Rien n'a été modifié : le code écrit pour cette demande est identique "
            "à la version {version}, qui reste la version actuelle."
        ),
        "identical_ask": (
            "J'ai réécrit le même code, donc je n'ai rien trouvé à modifier. Si ce "
            "que vous voyez diffère de ce que vous attendez, décrivez-moi les "
            "étapes suivies et ce qui s'affiche à l'écran, et je regarderai "
            "précisément cela."
        ),
    },
    "de": {
        "open": "„{title}“ öffnen (Version {version})",
        "download": "{filename} herunterladen",
        "temporary": (
            "Temporäre Vorschau: In der App eingegebene Daten werden nicht gespeichert."
        ),
        "unchanged": (
            "In diesem Zug wurde keine neue Version erstellt; Version {version} "
            "bleibt aktuell. Bitte mir die gewünschte Änderung noch einmal."
        ),
        "identical": (
            "Es wurde nichts geändert: Der für diese Anfrage geschriebene Code ist "
            "identisch mit Version {version}, die die aktuelle bleibt."
        ),
        "identical_ask": (
            "Ich habe denselben Code noch einmal geschrieben und deshalb nichts "
            "Änderungswürdiges gefunden. Wenn das, was Sie sehen, von Ihrer "
            "Erwartung abweicht, nennen Sie mir die Schritte und was auf dem "
            "Bildschirm erscheint, dann schaue ich genau darauf."
        ),
    },
    "es": {
        "open": "Abrir «{title}» (versión {version})",
        "download": "Descargar {filename}",
        "temporary": (
            "Vista previa temporal: los datos introducidos en la aplicación "
            "no se conservan."
        ),
        "unchanged": (
            "No se creó ninguna versión nueva en este turno; la versión {version} "
            "sigue siendo la actual. Pídeme otra vez el cambio que quieres."
        ),
        "identical": (
            "No se ha cambiado nada: el código escrito para esta petición es idéntico "
            "a la versión {version}, que sigue siendo la actual."
        ),
        "identical_ask": (
            "He escrito el mismo código otra vez, así que no encontré nada que "
            "cambiar. Si lo que ve difiere de lo que espera, dígame los pasos que "
            "siguió y qué aparece en la pantalla, y lo revisaré exactamente."
        ),
    },
    "it": {
        "open": "Apri «{title}» (versione {version})",
        "download": "Scarica {filename}",
        "temporary": (
            "Anteprima temporanea: i dati inseriti nell'app non vengono conservati."
        ),
        "unchanged": (
            "In questo turno non è stata creata una nuova versione; la versione "
            "{version} resta attuale. Chiedimi di nuovo la modifica che vuoi."
        ),
        "identical": (
            "Non è stato modificato nulla: il codice scritto per questa richiesta è "
            "identico alla versione {version}, che resta quella attuale."
        ),
        "identical_ask": (
            "Ho scritto lo stesso codice di nuovo, quindi non ho trovato nulla da "
            "modificare. Se ciò che vede è diverso da ciò che si aspetta, mi dica "
            "i passaggi seguiti e cosa compare sullo schermo, e guarderò proprio "
            "quello."
        ),
    },
}

HISTORY_REFERENCE_PATTERN = re.compile(
    r"/api/atlas/artifacts/([0-9a-f]{32})/versions/([1-9][0-9]{0,3})/open"
)
MAX_CURRENT_ARTIFACT_BYTES = 96 * 1024
# A stored app with embedded files (code plus at most
# app_media.MAX_TOTAL_BYTES in base64) is still read for a follow-up.
MAX_MEDIA_ARTIFACT_BYTES = 8 * 1024 * 1024
PROBLEM_DETAILS = {
    "dialog_api": "show the message inside the page",
    "script_syntax": "this script does not parse (check braces, brackets, "
    "parentheses and quotes); send the complete corrected file",
}


@dataclass(frozen=True)
class ArtifactReference:
    artifact_id: str
    version: int
    title: str


@dataclass(frozen=True)
class CurrentArtifact:
    message: Message
    artifact: dict[str, object] | None


CURRENT_ARTIFACT_PREFIX = (
    "Current app of this conversation (context only; Atlas applies changes to it "
    "and writes the links to its versions, never this answer): "
)
_FRAGMENT_TITLE = re.compile(r"#[^)\s]*?\btitle=([^&)\s]*)")


def history_references(messages: list[Message]) -> list[ArtifactReference]:
    """Every app link of the assistant turns before the last message, in order."""
    references: list[ArtifactReference] = []
    for message in messages[:-1]:
        if message.get("role") != "assistant":
            continue
        content = message.get("content")
        if not isinstance(content, str):
            continue
        for match in HISTORY_REFERENCE_PATTERN.finditer(content):
            # The footer link carries the stored title in its fragment; older
            # answers only have the link label.
            fragment = _FRAGMENT_TITLE.match(content, match.end())
            title_match = re.search(
                rf"\[([^\]]+)\]\({re.escape(match.group(0))}", content
            )
            title = (
                unquote(fragment.group(1))
                if fragment
                else title_match.group(1)
                if title_match
                else ""
            )
            references.append(
                ArtifactReference(match.group(1), int(match.group(2)), title)
            )
    return references


def parse_history_reference(messages: list[Message]) -> ArtifactReference | None:
    references = history_references(messages)
    return references[-1] if references else None


def current_artifact_message(
    messages: list[Message], identity: str | None
) -> CurrentArtifact | None:
    references = history_references(messages)
    if not references:
        return None
    # The latest link whose version is stored: a link Atlas never wrote (a
    # "version 5" an answer invented, staging 2026-11-20) must not hide the
    # stored app and make the next change rewrite it from scratch.
    version: artifact_store.ArtifactVersion | None = None
    for reference in dict.fromkeys(reversed(references)):
        try:
            version = artifact_store.read(
                reference.artifact_id, identity, reference.version
            )
        except ArtifactError:
            continue
        break
    if version is None:
        return CurrentArtifact(
            {
                "role": "system",
                "content": json.dumps(
                    {
                        "current_artifact": None,
                        "note": "previous app no longer stored (30-day retention)",
                    },
                    ensure_ascii=False,
                ),
            },
            None,
        )
    artifact: dict[str, object] = {
        "artifact_id": version.artifact_id,
        "version": version.version,
        "title": reference.title,
        "filename": version.filename,
    }
    content, media = _editable(version.content, version.size_bytes)
    if content is not None:
        artifact["content"] = content
        if media:
            artifact["media"] = media
    else:
        artifact["note"] = "source too large to include"
    visible = {
        key: value
        for key, value in artifact.items()
        if key not in {"artifact_id", "media"}
    }
    return CurrentArtifact(
        {
            "role": "system",
            "content": CURRENT_ARTIFACT_PREFIX
            + json.dumps({"current_artifact": visible}, ensure_ascii=False),
        },
        artifact,
    )


def _editable(content: bytes, size: int) -> tuple[str | None, list[object]]:
    """The source a follow-up reads, and the files embedded in it.

    Embedded files (packages/artifact_media.py) are read as short references:
    an app with samples stays editable while its bytes stay out of the model's
    context.
    """
    from packages.artifact_media import abstract

    if size > MAX_MEDIA_ARTIFACT_BYTES:
        return None, []
    text = content.decode("utf-8")
    readable, media = abstract(text) if "data:" in text else (text, [])
    if len(readable.encode("utf-8")) > MAX_CURRENT_ARTIFACT_BYTES:
        return None, []
    return readable, list(media)


# Markdown images; openings that share a failed label or target run are not
# retried ("![a](" repeated before one far ")" was quadratic).
_IMAGES = linear_scan.Scan(
    re.compile(r"!\[[^\]]*\]\([^)]*\)"),
    re.compile(r"!\["),
    linear_scan.bracket_link_failure(r"\]", r")", empty_label=True),
)


def clean_summary(summary: str) -> str:
    text = _IMAGES.sub("", summary)
    text = linear_scan.TAGS.sub("", text)
    return text.strip()


def _markdown_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def footer(
    artifact_id: str,
    version: int,
    title: str,
    filename: str,
    language: str,
    *,
    temporary: bool = True,
) -> str:
    labels = FOOTER_LABELS.get(language, FOOTER_LABELS["en"])
    prefix = f"/api/atlas/artifacts/{artifact_id}/versions/{version}"
    fragment = (
        f"atlas-view=app&title={quote(title, safe='')}"
        f"&artifact={artifact_id}&version={version}&slot=top"
    )
    open_label = labels["open"].format(title=title, version=version)
    download_label = labels["download"].format(filename=filename)
    text = (
        f"[{_markdown_escape(open_label)}]({prefix}/open#{fragment}) · "
        f"[{_markdown_escape(download_label)}]({prefix}/download)"
    )
    if version == 1 and temporary:
        text += f"\n\n{labels['temporary']}"
    return text


def unchanged_footer(version: int, language: str) -> str:
    labels = FOOTER_LABELS.get(language, FOOTER_LABELS["en"])
    return labels["unchanged"].format(version=version)


def identical_sentence(version: int, language: str) -> str:
    """Said when a change request produced the stored version again (C2)."""
    labels = FOOTER_LABELS.get(language, FOOTER_LABELS["en"])
    return labels["identical"].format(version=version)


def identical_answer(summary: str, version: int, language: str) -> str:
    """The answer of a change request whose code is the stored version again.

    The plain statement comes first. What the model wrote follows when it is
    not a claim of change (the answer to a question, a complaint or a line the
    person asked for: change_claims); when nothing is left, what the person can
    do next. The answer is never only the fixed statement.
    """
    from packages.addressing import addressed
    from services.orchestrator.change_claims import without_change_claims

    labels = FOOTER_LABELS.get(language, FOOTER_LABELS["en"])
    kept = without_change_claims(clean_summary(summary))
    # In the register of the turn (familiar when the person writes so).
    ask = addressed(labels["identical_ask"], language)
    return "\n\n".join((identical_sentence(version, language), kept or ask))


def compose_answer(
    prose: str,
    summary: str,
    artifact_id: str,
    version: int,
    title: str,
    filename: str,
    language: str,
    *,
    temporary: bool = True,
    note: str = "",
) -> str:
    cleaned = clean_summary(summary)
    parts = [part for part in (prose, cleaned, note) if part]
    parts.append(
        footer(artifact_id, version, title, filename, language, temporary=temporary)
    )
    return "\n\n".join(parts)
