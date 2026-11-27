"""Classification of chat turns for interactive app artefacts."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Literal

from packages.closing_intent import polite_closing
from packages.deferred_intent import creation_deferred
from packages.writing_intent import plain_writing


Intent = Literal["create", "modify", "available"]


_ARTIFACT_LINK = re.compile(
    r"/api/atlas/artifacts/[0-9a-f]{32}/versions/[1-9][0-9]{0,3}/open"
)
_NON_WEB = re.compile(
    r"\b(?:python|pygame|tkinter|java|kotlin|swift|c\+\+|c#|rust|golang|go"
    r"|unity|godot|android|ios|electron|flask|django|node server|backend)\b"
)
_EXPLANATION = re.compile(
    r"\b(?:comment|how|wie|como|come)\b.{0,40}\b(?:faire|make|machen|hacer|fare)\b"
    r"|\b(?:explique|explain|why|pourquoi|compare|erklare|spieg\w*|explica|perche|perche)\b"
)
_OFFICE = re.compile(
    r"\b(?:presentation|prasentation|presentacion|presentazione"
    r"|slides?|diapositives?|folien|diapositivas?|diapositive"
    r"|powerpoint|pptx"
    r"|word|docx"
    r"|excel|xlsx|spreadsheet|tableur|feuille de calcul"
    r"|tabellenkalkulation|hoja de calculo|foglio di calcolo"
    r"|pdf)\b"
)
_EXPLICIT_WEB = re.compile(r"\b(?:html|page web|web page|webseite|pagina web)\b")
_ATTACHMENT_OR_IMAGE = re.compile(
    r"\[attachment:|<attached_files>|\b(?:attachment|attached|piece jointe"
    r"|anhang|adjunto|allegato|image|imagen|immagine|bild|photo|foto)\b"
)
_DRAWING = re.compile(r"\b(?:dessine\w*|draw|zeichne\w*|dibuja\w*|disegna\w*)\b")
# SVG is drawn as code and rendered in the app panel, unlike an image request
# ("dessine un chat" goes to image generation; "dessine un chat en SVG" does not).
_SVG = re.compile(r"\bsvg\b")
_SHOW = re.compile(
    r"\b(?:affiche\w*|montre\w*|show|display|render|zeige\w*|muestra\w*|mostra\w*)\b"
)
# A request for a title ("suggest a title for a landing page") is a text for
# the chat. A title the person gives for the app ("put the title 'X' at the
# top") is part of the app: staging -185, with the routing call timed out, an
# app request that named its title was answered as code in the chat.
_TITLE_REQUEST = re.compile(
    r"\b(?:suggest\w*|propos\w*|find|give me|write|invent\w*|need|want|ideas? for"
    r"|ecris|ecrire|trouve\w*|donne\w*|sugger\w*|cherche\w*|idees? de"
    r"|schreib\w*|schlag\w*|finde\w*|gib mir|erfind\w*|brauche"
    r"|escrib\w*|propon\w*|sugier\w*|busca\w*|dame|necesito"
    r"|scriv\w*|suggerisc\w*|trova\w*|dammi)"
    r"\b(?:\W+\w+){0,3}?\W+(?:titles?|titres?|titel|titulos?|titol[oi])\b"
)
_PROJECT_OR_CODE_MODE = re.compile(
    r"\b(?:project|projet|projekt|proyecto|progetto|code mode|mode code"
    r"|api key|cle api|api-schlussel|clave api|chiave api)\b"
)
_QUESTION = re.compile(
    r"^\s*(?:can you|could you|pouvez vous|puedes|potresti|kannst du|why|pourquoi|what|quoi|was|que)\b|\?"
)
_ACTION = re.compile(
    r"\b(?:fais|cree|construis|code|programme|developpe|ecris|genere"
    r"|make|build|create|write|develop|generate"
    r"|erstelle|baue|programmiere|schreibe"
    r"|haz|crea|construye|programa|escribe"
    r"|fai|crea|costruisci|scrivi|programma)\b"
)
_APP = re.compile(
    r"\b(?:jeu|game|spiel|juego|gioco|application|appli|app|outil|tool"
    r"|calculatrice|calculator|rechner|calculadora|calcolatrice"
    r"|minuteur|timer|chronometre|pomodoro|uhr"
    r"|quiz|flashcards|karteikarten|tarjetas|flashcard"
    r"|simulateur|simulator|simulador|simulatore"
    r"|convertisseur|converter|konverter|convertidor|convertitore"
    r"|visualisation|graphique interactif|chart|diagramme interactif"
    r"|interaktive visualisierung|grafico interactivo|grafico interattivo"
    r"|todo list|liste de taches|aufgabenliste|lista de tareas|lista di attivita"
    r"|page web|landing page|webseite|pagina web|pagina web"
    r"|tableau de bord|dashboard"
    r"|snake|tetris|pong|morpion|sudoku|2048|casse briques|demineur)\b"
    r"|\b(?:html|javascript|canvas)\b"
)
_CHANGE = re.compile(
    # Stems: a request worded as a question takes the infinitive ("peux-tu
    # ajouter", "puedes añadir", "potresti aggiungere").
    r"\b(?:change|modifi\w*|ajout\w*|enlev\w*|retir\w*|remplac\w*|corrig\w*"
    r"|rends|rendre|mets|mettre|rajout\w*|integr\w*|renomm\w*|amelior\w*|repar\w*"
    r"|add|remove|replace|fix|make it|include|insert|rename|update|tweak|adjust"
    r"|andere|fuge|entferne|mache"
    r"|einbau\w*|hinzufug\w*|erganz\w*|einfug\w*|umbenenn\w*|anpass\w*"
    r"|korrigier\w*|repari\w*|entfern\w*|aender\w*"
    r"|cambi\w*|anad\w*|quita\w*|agreg\w*|incluy\w*|inclui\w*|renombr\w*"
    r"|arregl\w*|correg\w*"
    r"|aggiung\w*|rimuov\w*|togli\w*|inserisc\w*|inserir\w*|includ\w*"
    r"|rinomin\w*|sistema)\b"
)
# A request to check the conversation's app in use, and fix it if needed: the
# app turn reads, checks and, only when the code changes, stores a version.
_CHECK = re.compile(
    r"\b(?:double[- ]check|check|test|try|verify"
    r"|verifie\w*|teste\w*|controle\w*|essaie"
    r"|prufe?\w*|uberpruf\w*|teste?|probier\w*|kontrollier\w*"
    r"|comprueba|prueba|probar|verifica|controlla|prova)\b"
)
# "Can you add ...?", "Kannst du ... einbauen?": a request in the form of a
# question. Staging -184 (class suite): such follow-ups to the conversation's
# app were read as questions about it when the routing call had timed out, and
# a plain answer claimed a change that was never made.
# A greeting may open the request ("salut ! tu peux me faire ...?").
_GREETING = (
    r"(?:(?:hi|hello|hey|hiya|salut|bonjour|bonsoir|coucou|hallo|servus|moin"
    r"|hola|buenas|ciao|salve|buongiorno)(?:\W+(?:there|toi|a toi|zusammen))?\W+)?"
)
_POLITE = re.compile(
    r"^\W*" + _GREETING + r"(?:(?:please|pls|bitte|stp|svp|por favor|per favore)\W+)?"
    r"(?:can you|could you|would you|will you|can u"
    r"|kannst du|konntest du|konnen sie|konnten sie|wurdest du"
    r"|peux[- ]tu|pourrais[- ]tu|pouvez[- ]vous|pourriez[- ]vous|tu peux|tu pourrais"
    r"|vous pouvez|est[- ]ce que tu peux|est[- ]ce que vous pouvez"
    r"|puedes|podrias|puede usted|podria"
    r"|puoi|potresti|puo|potrebbe)\b"
)


def app_intent(text: str, history: Sequence[Mapping[str, object]]) -> Intent | None:
    normalized = _normalized(text)
    if not normalized:
        return None
    if creation_deferred(text):
        return None
    # A thanks or a compliment after an app asks for nothing: a chat answer,
    # never the app again (staging 2026-11-16).
    if polite_closing(text):
        return None
    if _OFFICE.search(normalized) and not _EXPLICIT_WEB.search(normalized):
        return None
    svg = svg_intent(normalized, history)
    if svg is not None:
        return svg
    if any(
        pattern.search(normalized)
        for pattern in (
            _NON_WEB,
            _EXPLANATION,
            _ATTACHMENT_OR_IMAGE,
            _DRAWING,
            _TITLE_REQUEST,
            _PROJECT_OR_CODE_MODE,
        )
    ):
        return None
    has_artifact = _has_artifact(history)
    checks = (
        has_artifact
        and bool(_APP.search(normalized))
        and bool(_CHECK.search(normalized))
    )
    if not _is_app_request(normalized) and not checks:
        return None
    if _QUESTION.search(normalized) and not _polite_request(normalized, has_artifact):
        return None
    if not has_artifact:
        # "Write a short message to ..." is a text for the chat: a writing verb
        # alone is not an app request (staging 2026-11-16).
        return None if plain_writing(normalized) else "create"
    if _CHANGE.search(normalized) or checks:
        return "modify"
    return None if plain_writing(normalized) else "available"


# The infinitive a request worded as a question takes ("tu peux me faire",
# "kannst du ... bauen", "puedes hacer"): a request only with the app named.
_BUILD_INFINITIVE = re.compile(
    r"\b(?:faire|creer|construire|coder|programmer|developper|ecrire|generer"
    r"|preparer|put together|whip up|code up"
    r"|machen|bauen|erstellen|programmieren|schreiben|basteln|entwickeln"
    r"|hacer|crear|construir|programar|escribir|preparar|desarrollar"
    r"|fare|creare|costruire|programmare|scrivere|preparare|sviluppare)\b"
)


def _polite_request(normalized: str, has_artifact: bool) -> bool:
    """A request worded as a question ("can you add ...?") that asks to do.

    It opens with a polite request form and carries a building or change verb,
    or, for the conversation's app, a check of the app ("can you double-check
    it in the app?"). "Why", "what" or a bare "change the colour?" stay questions.
    """
    if _POLITE.search(normalized) is None:
        return False
    if _CHANGE.search(normalized) or _ACTION.search(normalized):
        return True
    if _BUILD_INFINITIVE.search(normalized) and _APP.search(normalized):
        # "Tu peux me faire une appli ...?": the infinitive of a building
        # verb, with the app it builds named.
        return True
    return has_artifact and bool(_APP.search(normalized) and _CHECK.search(normalized))


def svg_intent(
    normalized: str, history: Sequence[Mapping[str, object]]
) -> Intent | None:
    """An SVG drawing, or a request to show the SVG just given, is an app turn."""
    if _SVG.search(normalized) and not _EXPLANATION.search(normalized):
        if not _has_artifact(history):
            return "create"
        return "modify" if _CHANGE.search(normalized) else "create"
    if _SHOW.search(normalized) and _last_answer_has_svg(history):
        return "create"
    return None


def svg_requested(text: str) -> bool:
    """The request names SVG: a drawing written as code for the app panel."""
    return _SVG.search(_normalized(text)) is not None


def _last_answer_has_svg(history: Sequence[Mapping[str, object]]) -> bool:
    for message in reversed(history):
        if message.get("role") != "assistant":
            continue
        content = message.get("content", "")
        return isinstance(content, str) and "<svg" in content.casefold()
    return False


def _has_artifact(history: Sequence[Mapping[str, object]]) -> bool:
    for message in history:
        content = message.get("content", "")
        if isinstance(content, str) and _ARTIFACT_LINK.search(content):
            return True
    return False


def _is_app_request(normalized: str) -> bool:
    if _APP.search(normalized):
        return True
    return (
        _ACTION.search(normalized) is not None or _CHANGE.search(normalized) is not None
    )


def _normalized(text: str) -> str:
    return "".join(
        character
        for character in unicodedata.normalize("NFKD", text.casefold())
        if not unicodedata.combining(character)
    )
