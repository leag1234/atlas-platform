"""Measured facts about an app file Atlas delivers, and the answer held to them.

Staging -178: the person said "it does not work, nothing shows". The turn
opened the current version again, found nothing wrong and asked what they
saw, which is right; but it also said the flags were "embedded only inside
Atlas" and "cannot load" in an ordinary browser. The stored file, which is
the downloaded file, carried the six pictures as data: URIs and loaded
nothing from the network. The model had read the file as Atlas shows it to
the writer (atlas-media references, an "atlasMedia" helper "provided by
Atlas") and inferred a property of Atlas's own artifact.

Two parts, both on the file as stored:

- ``measure`` counts what code can see: size, files embedded inline,
  addresses the file loads from the network (scripts, pictures, styles,
  fetches), and ``model_note`` gives them to the writer on every follow-up,
  so an explanation rests on them;
- ``checked_answer`` reads the answer's statements about where the file
  works ("only inside Atlas", "cannot load in an ordinary browser", "needs
  an internet connection") and removes those the facts contradict, with one
  code-written sentence that states the fact instead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Addresses a page loads (a link the person may follow is not a load).
_LOADS = (
    re.compile(r"""<script\b[^>]*?\bsrc\s*=\s*["']?((?:https?:)?//[^"'\s>]+)""", re.I),
    re.compile(r"""\bsrc\s*=\s*["']?((?:https?:)?//[^"'\s>]+)""", re.I),
    re.compile(r"""<link\b[^>]*?\bhref\s*=\s*["']?((?:https?:)?//[^"'\s>]+)""", re.I),
    re.compile(r"""\burl\(\s*["']?((?:https?:)?//[^"')\s]+)""", re.I),
    re.compile(r"""@import\s*(?:url\(\s*)?["']((?:https?:)?//[^"']+)""", re.I),
    re.compile(
        r"""\b(?:fetch|importScripts|EventSource|WebSocket|import)\s*\(\s*["'`]"""
        r"""((?:https?:|wss?:)?//[^"'`\s]+)""",
        re.I,
    ),
    re.compile(r"""\bfrom\s+["']((?:https?:)?//[^"']+)["']""", re.I),
    re.compile(r"""\.open\(\s*["'][A-Z]+["']\s*,\s*["']((?:https?:)?//[^"']+)""", re.I),
)
_SCRIPT = _LOADS[0]
# Fallback states a file may have: how code sees each one, and the claims
# an answer makes of it. Staging -181: "I also added a visible message if
# JavaScript is off or blocked" on a version with no <noscript> at all.
_ERROR_LISTENER = r"""addEventListener\(\s*['"](?:error|unhandledrejection)['"]"""
STATES: dict[str, tuple[re.Pattern[str], re.Pattern[str]]] = {
    "no_script": (
        re.compile(r"<noscript\b[^>]*>\s*[^<\s]", re.I),
        re.compile(
            r"\bJavaScript\b[^.!?\n]{0,30}\b(?:désactivé|bloqué|coupé|absent|"
            r"disabled|blocked|turned off|switched off|off|deaktiviert|blockiert|"
            r"desactivado|bloqueado|disattivato|bloccato)|"
            r"\b(?:sans|without|ohne|sin|senza)\s+JavaScript\b|<noscript|\bnoscript\b",
            re.I,
        ),
    ),
    "image_error": (
        re.compile(r"\bonerror\b|" + _ERROR_LISTENER, re.I),
        re.compile(
            r"\b(?:si|quand|lorsque)\b[^.!?\n]{0,40}\b(?:image|drapeau|photo|"
            r"illustration)s?\b[^.!?\n]{0,30}\b(?:ne\s+(?:se\s+)?charge|échoue|"
            r"manque|est\s+absente)|"
            r"\b(?:if|when)\b[^.!?\n]{0,30}\b(?:image|picture|flag|photo)s?\b"
            r"[^.!?\n]{0,20}\b(?:fails?|doesn't|does not|can't|cannot|won't)\b"
            r"[^.!?\n]{0,10}\bload|"
            r"\b(?:image|picture|flag)s?\s+(?:fails?|failing)\s+to\s+load",
            re.I,
        ),
    ),
    "script_error": (
        re.compile(r"\btry\s*\{|\bwindow\.onerror\b|" + _ERROR_LISTENER, re.I),
        re.compile(
            r"\b(?:si|quand|lorsque|même si)\b[^.!?\n]{0,20}\b(?:le\s+|un\s+)?script\b"
            r"[^.!?\n]{0,20}\b(?:plante|échoue|peine|bloque|ne\s+(?:se\s+)?charge)|"
            r"\b(?:if|when|even if)\b[^.!?\n]{0,20}\b(?:the\s+)?script\b"
            r"[^.!?\n]{0,20}\b(?:fails|crashes|breaks|errors|struggles)",
            re.I,
        ),
    ),
}
# A claim of a fallback state says the file shows something then; "check
# that JavaScript is not disabled" is advice, not such a claim.
_SHOWS = re.compile(
    r"message|avertissement|affich|visible|texte|vignette|remplac|retomb|"
    r"à la place|\bshow|display|warning|notice|placeholder|fallback|instead|"
    r"Hinweis|Meldung|anzeig|mensaje|aviso|muestra|messaggio|avviso|mostra",
    re.I,
)
_STATE_WORDS = {
    "no_script": "a message shown without JavaScript (<noscript>)",
    "image_error": "what shows when a picture fails to load (an error handler)",
    "script_error": "what shows when the script fails (try/catch or an error handler)",
}
_INLINE = re.compile(r"data:(?:image|audio|video|font)/[\w.+-]+;base64,", re.I)
MAX_LISTED = 6


@dataclass(frozen=True)
class FileFacts:
    size: int
    # Files carried inside the file as data: URIs.
    inline: int
    # Their names, when Atlas embedded the person's files (artifact_media).
    names: list[str] = field(default_factory=list)
    # Addresses the file loads from the network, and those that are scripts.
    external: list[str] = field(default_factory=list)
    scripts: list[str] = field(default_factory=list)
    # The fallback states the file has (STATES): what it shows without
    # JavaScript, when a picture fails to load, when its script fails.
    states: frozenset[str] = frozenset()

    @property
    def self_contained(self) -> bool:
        return not self.external


def measure(document: str, names: list[str] | None = None) -> FileFacts:
    """The facts of a stored app file (media embedded, as it is downloaded)."""
    external: list[str] = []
    for pattern in _LOADS:
        for match in pattern.finditer(document):
            if match.group(1) not in external:
                external.append(match.group(1))
    scripts = list(dict.fromkeys(m.group(1) for m in _SCRIPT.finditer(document)))
    return FileFacts(
        len(document.encode("utf-8")),
        len(_INLINE.findall(document)),
        list(names or []),
        external,
        scripts,
        frozenset(
            name for name, (pattern, _) in STATES.items() if pattern.search(document)
        ),
    )


def model_note(facts: FileFacts, version: int) -> str:
    """The facts, as the writer reads them before it explains anything."""
    lines = [
        f"Measured facts about version {version} as stored (the file the user "
        "downloads is this same file, with every embedded file inside it):",
        f"- size: {max(1, facts.size // 1000)} KB;",
    ]
    if facts.inline:
        named = ", ".join(facts.names[:MAX_LISTED])
        lines.append(
            f"- files embedded inline as data: URIs: {facts.inline}"
            + (f" ({named})" if named else "")
            + "; the atlas-media references and the atlasMedia helper in the "
            "source above are written into the stored file itself;"
        )
    if facts.self_contained:
        lines.append(
            "- it loads nothing from the network: it works the same in the side "
            "panel, downloaded, in any browser, and offline."
        )
    else:
        lines.append(
            "- it loads these addresses from the network (blocked in the side "
            "panel, they need an internet connection elsewhere): "
            + ", ".join(facts.external[:MAX_LISTED])
            + (
                "; scripts among them: " + ", ".join(facts.scripts[:MAX_LISTED])
                if facts.scripts
                else ""
            )
            + "."
        )
    has = [_STATE_WORDS[name] for name in STATES if name in facts.states]
    missing = [_STATE_WORDS[name] for name in STATES if name not in facts.states]
    lines.append(
        "- fallback states it has: "
        + ("; ".join(has) if has else "none")
        + (
            "; it does not have: "
            + "; ".join(missing)
            + " (never say it shows such a message unless you write it in the file)"
            if missing
            else ""
        )
        + "."
    )
    lines.append(
        "Base every explanation about the file on these facts and on what the "
        "preview check measured. Never say the file only works inside Atlas, "
        "cannot load outside it or needs the internet unless these facts say "
        "so. When nothing wrong was measured and the user says it does not "
        "work, keep the version, ask what they see, and suggest only causes "
        "these facts allow: an older version or an older download opened, the "
        "browser cache, a script blocked by a browser extension or setting."
    )
    return "\n".join(lines)


# Statements about where the file works. "place": it works only in Atlas or
# not in an ordinary browser (false for a file that loads nothing);
# "network": it needs an internet connection (false for the same file).
_NEGATION = re.compile(
    r"\bne\b|\bn'|\bpas\b|impossible|\bnot\b|n't\b|\bcannot\b|\bunable\b|"
    r"\bnicht\b|\bkein|\bno\b|\bnon\b|\bnunca\b|\bmai\b",
    re.I,
)
_PLACE_ALWAYS = re.compile(
    r"\b(?:uniquement|seulement|exclusivement)\b[^.!?\n]{0,40}\bAtlas\b|"
    r"\bne\b[^.!?\n]{0,60}\bqu'(?:à l'intérieur d'|au sein d'|dans |avec |sur |depuis )"
    r"(?:l'aperçu d'|le panneau d'|la vue latérale d')?Atlas\b|"
    r"\bonly\b[^.!?\n]{0,30}\b(?:inside|in|within|through|from)\s+(?:the\s+)?Atlas\b|"
    r"\bnur\b[^.!?\n]{0,30}\b(?:in|innerhalb)\b[^.!?\n]{0,15}\bAtlas\b|"
    r"\b(?:solo|solamente|únicamente|soltanto)\b[^.!?\n]{0,30}\b(?:en|dentro de|dentro di|in)\s+Atlas\b",
    re.I,
)
_PLACE_NEGATED = re.compile(
    r"\b(?:en dehors|hors|à l'extérieur) d'Atlas\b|\boutside (?:of )?Atlas\b|"
    r"\baußerhalb von Atlas\b|\bfuera de Atlas\b|\bfuori da Atlas\b|"
    r"\b(?:navigateur|browser|navegador)\s+(?:classique|normal|ordinaire|standard|"
    r"habituel|regular|ordinary|usual|other|autre|normale|comune|habitual)\b|"
    r"\bpeu\w*\s+pas\s+(?:s'y\s+|se\s+)?(?:charg|afficher|s'afficher)\w*|"
    r"\b(?:can't|cannot|won't|will not)\s+(?:be\s+)?(?:load|display|show)\w*",
    re.I,
)
# "It works outside Atlas too": a negation nearby is not the claim.
_ALSO = re.compile(r"\b(?:aussi|également|also|too|as well|auch|también|anche)\b", re.I)
_NETWORK = re.compile(
    r"\b(?:connexion|accès)\s+(?:à\s+)?internet\b|\bbesoin d'internet\b|"
    r"\binternet (?:connection|access)\b|\b(?:needs?|requires?) (?:the )?internet\b|"
    r"\bInternetverbindung\b|\bconexión a internet\b|\bconnessione (?:a )?internet\b",
    re.I,
)

# The fact said instead, in the person's language (no form of address).
CORRECTION = {
    "fr": (
        "Précision : le fichier de l'application est autonome, tout ce qu'il "
        "utilise y est intégré et il ne charge rien depuis Internet ; il "
        "fonctionne donc aussi une fois téléchargé, dans n'importe quel "
        "navigateur, sans connexion."
    ),
    "en": (
        "To be precise: the app file is self-contained; everything it uses is "
        "embedded in it and it loads nothing from the internet, so it also "
        "works once downloaded, in any browser, offline."
    ),
    "de": (
        "Genauer gesagt: Die App-Datei ist eigenständig; alles, was sie "
        "verwendet, ist eingebettet, und sie lädt nichts aus dem Internet. Sie "
        "funktioniert daher auch heruntergeladen, in jedem Browser, ohne "
        "Verbindung."
    ),
    "es": (
        "Para precisar: el archivo de la aplicación es autónomo; todo lo que usa "
        "está integrado y no carga nada de internet, así que también funciona "
        "descargado, en cualquier navegador y sin conexión."
    ),
    "it": (
        "Per precisione: il file dell'app è autonomo; tutto ciò che usa è "
        "incorporato e non carica nulla da internet, quindi funziona anche "
        "scaricato, in qualsiasi browser e senza connessione."
    ),
}
# A sentence ends at its punctuation followed by a space ("file.html" does not).
_SENTENCE = re.compile(r"\S.*?(?:[.!?]+(?=\s|$)|\Z)", re.S)


def contradicted(sentence: str, facts: FileFacts) -> str:
    """The kind of claim a sentence makes that the facts contradict, or ""."""
    sentence = sentence.replace("\u2019", "'")
    for name, (_, claim) in STATES.items():
        if (
            name not in facts.states
            and claim.search(sentence)
            and _SHOWS.search(sentence)
        ):
            return name
    if not facts.self_contained:
        return ""
    if _PLACE_ALWAYS.search(sentence):
        return "place"
    if (
        _PLACE_NEGATED.search(sentence)
        and _NEGATION.search(sentence)
        and not _ALSO.search(sentence)
    ):
        return "place"
    if _NETWORK.search(sentence):
        return "network"
    return ""


def checked_answer(text: str, facts: FileFacts, language: str) -> tuple[str, list[str]]:
    """The answer without the statements the facts contradict, and their kinds.

    A paragraph made mostly of statements on where the file works goes whole
    (what follows from a false cause is advice for it); otherwise only those
    sentences go, as a claimed fallback message does. The corrected fact on
    where the file works is said once, at the end of the answer's prose.
    """
    kinds: list[str] = []
    kept: list[str] = []
    for paragraph in text.split("\n\n"):
        if paragraph.lstrip().startswith(("```", "|", "    ")):
            kept.append(paragraph)
            continue
        sentences = [match.group(0) for match in _SENTENCE.finditer(paragraph)]
        verdicts = [contradicted(sentence, facts) for sentence in sentences]
        found = [kind for kind in verdicts if kind]
        if not found:
            kept.append(paragraph)
            continue
        kinds += found
        if 2 * len(found) >= len(sentences) and {"place", "network"} & set(found):
            continue
        rest = " ".join(
            sentence
            for sentence, kind in zip(sentences, verdicts, strict=True)
            if not kind
        ).strip()
        if rest:
            kept.append(rest)
    if not kinds:
        return text, []
    if {"place", "network"} & set(kinds):
        kept.append(CORRECTION.get(language, CORRECTION["en"]))
    return "\n\n".join(part for part in kept if part.strip()), kinds
