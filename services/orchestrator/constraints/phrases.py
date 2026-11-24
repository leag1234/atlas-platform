"""The constraints code reads by itself from the person's words.

Phrase classes in English, French, German, Spanish and Italian, kept to forms
whose reading is unambiguous (high precision first: a missed constraint is
left to the model reading, a wrong one would make Atlas repair a right
answer). The exact text a constraint names is always taken from the
person's quotation marks, never paraphrased.

- the reply begins or ends with a quoted text ("commencez votre réponse par
  « ... »", "start with "..."", "beginne mit „...“");
- a quoted line goes under the delivered file ("schreib unter die Datei
  „...“", "écris sous le fichier « ... »");
- a quoted word is not used ("without the word "..."");
- the reply is one sentence, or N sentences, at most or at least;
- no lead-in ("sans intro", "no preamble", "ohne Einleitung");
- the requested output alone (only_request.asks_only);
- nothing is made or changed in this turn (packages.deferred_intent, and
  "no need to redo the file", "before changing anything").

A form constraint stated next to a file request ("a case in one sentence on
each slide") is about the file: code leaves it to the model reading unless a
verb of answering or telling governs it.
"""

from __future__ import annotations

import re
from typing import Iterator

from services.orchestrator.constraints.model import Constraint, fold

# Quoted text in the person's message: the exact words a constraint names.
_QUOTED = re.compile(
    r"«\s*(?P<a>[^»\n]{1,300}?)\s*»"
    r"|“(?P<b>[^”\n]{1,300}?)”"
    r"|„(?P<c>[^“”\"\n]{1,300}?)[“”\"]"
    r"|\"(?P<d>[^\"\n]{1,300}?)\""
    r"|`(?P<e>[^`\n]{1,300}?)`"
    r"|‘(?P<f>[^’\n]{1,300}?)’"
    # Plain single quotes, set apart from apostrophes by what surrounds them.
    r"|(?<!\w)'(?P<g>[^'\n]{1,120}?)'(?!\w)"
)

_ANSWER = r"(?:reponse|reply|answer|response|message|antwort|respuesta|risposta)"
_POSS = r"(?:(?:ta|votre|la|ton|your|the|deine|ihre|die|dein|tu|su|la|tua|la tua|vostra)\s)?"

_STARTS = re.compile(
    rf"(?:\b(?:commence\w*+|debute\w*+)\s(?:{_POSS}{_ANSWER}\s)?(?:par|avec)"
    rf"|\b(?:start|begin)\w*+\s(?:{_POSS}{_ANSWER}\s)?(?:with|by)(?:\sthe\swords?)?"
    rf"|\bbeginn\w*+\s(?:sie\s)?(?:{_POSS}{_ANSWER}\s)?mit"
    rf"|\b(?:empie\w*+|empez\w*+|comienz\w*+|comenz\w*+)\s(?:{_POSS}{_ANSWER}\s)?(?:con|por)"
    rf"|\b(?:inizi\w*+|cominci\w*+)\s(?:{_POSS}{_ANSWER}\s)?(?:con|da|per))"
    r"(?:\s(?:la|le|les|the|den|die|das|el|lo|il)\s?(?:phrase|mots?|words?|satz|"
    r"worten|frase|parole|palabras)?)?\s*+:?\s*+$"
)
_ENDS = re.compile(
    rf"(?:\b(?:termine\w*+|finis\w*+|conclu\w*+)\s(?:{_POSS}{_ANSWER}\s)?(?:par|avec)"
    rf"|\b(?:end|finish|close)\w*+\s(?:{_POSS}{_ANSWER}\s)?with"
    rf"|\b(?:beend\w*+|schliess\w*+)\s(?:sie\s)?(?:{_POSS}{_ANSWER}\s)?mit"
    rf"|\b(?:termin\w*+|acab\w*+|finaliz\w*+)\s(?:{_POSS}{_ANSWER}\s)?con"
    rf"|\b(?:termin\w*+|chiud\w*+|finisc\w*+)\s(?:{_POSS}{_ANSWER}\s)?con)"
    r"(?:\s(?:la|le|the|den|die|el|il)\s?(?:phrase|mots?|words?|satz|frase|parole)?)?"
    r"\s*+:?\s*+$"
)
_FILE_NOUN = (
    r"(?:fichier|lien|fiche|document|pdf|telechargement|file|link|download|datei|"
    r"link|dokument|archivo|enlace|documento|descarga|file|collegamento)"
)
_WRITE = (
    r"(?:ecri\w*+|mets|mettez|met|ajoute\w*+|indique\w*+|note\w*+|"
    r"write|put|add|note|state|"
    r"schreib\w*+|setz\w*+|notier\w*+|vermerk\w*+|"
    r"escrib\w*+|pon|ponga|pone\w*+|anad\w*+|indica\w*+|"
    r"scrivi\w*+|metti\w*+|aggiungi\w*+|indica\w*+)"
)
_UNDER = re.compile(
    rf"\b{_WRITE}\s(?:(?:moi|mir|me|nous|uns|bitte|please|juste|just|bien)\s)*+"
    r"(?:"
    rf"(?:en\s)?(?:dessous|sous)\s(?:du|le|la|de\sla|de\sl)\s?{_FILE_NOUN}"
    rf"|(?:below|under|underneath|after)\s(?:the|your)\s{_FILE_NOUN}"
    rf"|unter\s(?:die|der|den|dem)\s{_FILE_NOUN}\w*"
    rf"|(?:debajo|bajo|despues)\s(?:del|de\sla)\s{_FILE_NOUN}"
    rf"|(?:sotto|dopo)\s(?:il|al|lo|la)\s{_FILE_NOUN}"
    r"|en\sdessous|dessous|below\sit|underneath|darunter|debajo|sotto"
    r")\b[^.!?\n]{0,12}$"
)
_EXCLUDES = re.compile(
    r"(?:\b(?:sans|pas\sde|n\sutilise\w*+\spas|evite\w*+|jamais)\s(?:le\s|les\s)?"
    r"(?:mots?|termes?|expressions?)"
    r"|\b(?:without|don\st\suse|do\snot\suse|avoid|never\suse|never\swrite)\s(?:the\s)?"
    r"(?:words?|terms?|phrases?|expressions?)"
    r"|\b(?:ohne|vermeide\w*+|nie|niemals|kein\w*+)\s(?:das\s|die\s|den\s)?"
    r"(?:wort|worter|woerter|begriff\w*+|ausdruck\w*+)"
    r"|\b(?:sin|evita\w*+|no\suses|nunca)\s(?:la\s|las\s|el\s)?"
    r"(?:palabras?|terminos?|expresion\w*+)"
    r"|\b(?:senza|evita\w*+|non\susare|mai)\s(?:la\s|le\s|il\s)?"
    r"(?:parol\w*+|termin\w*+|espression\w*+))"
    r"\s*+:?\s*+$"
)

_ONE = r"(?:une|un|1|one|a|single|einem|einen|eine|ein|una|uno)"
_NUMBERS = {
    "1": 1, "un": 1, "une": 1, "one": 1, "a": 1, "single": 1, "einem": 1,
    "einen": 1, "eine": 1, "ein": 1, "una": 1, "uno": 1,
    "deux": 2, "two": 2, "zwei": 2, "dos": 2, "due": 2,
    "trois": 3, "three": 3, "drei": 3, "tres": 3, "tre": 3,
    "quatre": 4, "four": 4, "vier": 4, "cuatro": 4, "quattro": 4,
    "cinq": 5, "five": 5, "funf": 5, "fuenf": 5, "cinco": 5, "cinque": 5,
}  # fmt: skip
_COUNTED = r"(?:\d{1,2}|" + "|".join(sorted(_NUMBERS, key=len, reverse=True)) + ")"
_SENTENCE = r"(?:phrases?|sentences?|satz|satze|saetze|frases?|frasi)"
_AT_MOST = (
    r"(?:au\splus|maximum|pas\splus\sde|at\smost|no\smore\sthan|max(?:imum)?|"
    r"hochstens|hoechstens|maximal|como\smaximo|no\smas\sde|al\smassimo|non\spiu\sdi)"
)
_AT_LEAST = r"(?:au\smoins|at\sleast|mindestens|al\smenos|almeno)"
_TELL = (
    r"(?:donne\w*+|dis|dites|ecri\w*+|repond\w*+|resume\w*+|formule\w*+|propose\w*+|"
    r"give|tell|write|answer|reply|say|sum\w*+|put\sit|"
    r"gib|sag\w*+|schreib\w*+|antwort\w*+|fass\w*+|formulier\w*+|nenn\w*+|"
    r"da(?:me)?|dime|di|escrib\w*+|respond\w*+|resum\w*+|"
    r"dammi|dimmi|scrivi\w*+|rispond\w*+|riassum\w*+)"
)
_SENTENCES = re.compile(
    rf"\b(?:(?P<most>{_AT_MOST})\s|(?P<least>{_AT_LEAST})\s)?"
    rf"(?:(?:en|in|dans|con)\s)?(?:(?:juste|seulement|only|just|nur|solo|solamente|"
    rf"soltanto|une\sseule|a\ssingle)\s)?(?P<n>{_COUNTED})\s"
    rf"(?:(?:seule|single|einzigen?|sola|kurzen?|courte|short|breve|corta)\s)?{_SENTENCE}\b"
    r"(?!\s(?:ou|or|oder|o|od)\s)"
)
# "two sentences at most": the bound after the count.
_AFTER_MOST = re.compile(
    r"\s(?:au\splus|maximum|max|at\smost|or\s(?:less|fewer)|ou\smoins|hochstens|"
    r"hoechstens|maximal|como\smaximo|o\smenos|al\smassimo|o\smeno)\b"
)
_AFTER_LEAST = re.compile(
    r"\s(?:au\smoins|minimum|at\sleast|or\smore|ou\splus|mindestens|como\sminimo|"
    r"al\smenos|o\smas|almeno|o\spiu)\b"
)
_TELL_BEFORE = re.compile(rf"\b{_TELL}\b[^.!?\n]{{0,40}}$")
_NO_INTRO = re.compile(
    r"\b(?:sans|pas\sd|aucune?)\s?(?:intro|introduction|preambule|phrase\sd\sintro\w*+)\b"
    r"|\b(?:no|without|skip\sthe)\s(?:intro|introduction|preamble|lead\sin)\b"
    r"|\b(?:ohne|keine)\s(?:einleitung|vorrede|vorwort|einfuhrung|einfuehrung)\b"
    r"|\b(?:sin|ninguna)\s(?:intro|introduccion|preambulo)\b"
    r"|\b(?:senza|nessuna)\s(?:intro|introduzione|preambolo)\b"
)
_NO_CHANGE = re.compile(
    r"\bpas\sbesoin\sde\s(?:la\s|le\s|les\s)?(?:refaire|modifier|toucher|changer|"
    r"regenerer|recreer)\b"
    r"|\bne\s(?:le|la|les|l)?\s?(?:refai\w*+|regenere\w*+|recree\w*+)\spas\b"
    r"|\bsans\s(?:refaire|modifier|toucher\sa|regenerer)\s(?:le|la|les|l)\s?"
    r"(?:fichier|fiche|document|pdf|diaporama|presentation|appli\w*+|image|tableau)"
    r"|\bavant\sde\s(?:changer|modifier|toucher)\s(?:quoi\sque\sce\ssoit|rien|a\squoi)"
    r"|\bno\sneed\sto\s(?:redo|remake|regenerate|recreate|change|edit|touch|modify|"
    r"update)\b"
    r"|\b(?:don\st|do\snot)\s(?:redo|remake|regenerate|recreate)\b"
    r"|\bbefore\s(?:changing|you\schange|modifying|editing)\sanything\b"
    r"|\bnoch\s(?:nichts|nicht)\s(?:andern|aendern|anfassen|erstellen|machen|exportieren)\b"
    r"|\bnicht\sneu\s(?:erstellen|machen|exportieren|generieren|bauen)\b"
    r"|\bkeine\sneue\s(?:datei|version|folie\w*+)\b"
    r"|\bbevor\s(?:du|sie)\s(?:etwas|irgendwas|was)\s(?:anderst|aenderst|andern|aendern)"
    r"|\bantes\sde\s(?:cambiar|tocar|modificar)\snada\b"
    r"|\bno\s(?:hace\sfalta|es\snecesario)\s(?:rehacer|cambiar|tocar|modificar|regenerar)"
    r"|\bno\s(?:lo|la)\s(?:rehagas|regeneres)\b"
    r"|\bprima\sdi\s(?:cambiare|toccare|modificare)\s(?:nulla|niente|qualcosa)\b"
    r"|\bnon\s(?:serve|c\se\sbisogno\sdi)\s(?:rifare|modificare|toccare|rigenerare)"
    r"|\bnon\s(?:rifarlo|rifarla|rigenerarlo)\b"
)
# Words that ask for a file: a form constraint next to them is about the file.
_FILE_REQUEST = re.compile(
    r"\b(?:fichier|pdf|docx|word|powerpoint|pptx|diapo\w*+|diaporama|presentation|"
    r"document|fiche|affiche|tableur|excel|xlsx|appli\w*+|page\sweb|"
    r"file|slides?|deck|spreadsheet|poster|app|web\s?app|"
    r"datei|folien?|prasentation|praesentation|tabelle|plakat|"
    r"archivo|diapositivas?|presentacion|hoja|cartel|aplicacion|"
    r"diapositive|presentazione|foglio|manifesto|applicazione)\b"
)
_CLAUSE_END = re.compile(r"[.!?;\n]")


def _sentences(text: str) -> Iterator[tuple[str, str]]:
    """The person's sentences, as written and folded."""
    for part in re.split(r"(?<=[.!?\n])\s+", text):
        if part.strip():
            yield part.strip(), fold(part)


def _clause_before(text: str, start: int, width: int = 90) -> str:
    """The text of the clause that ends where a quotation begins."""
    before = text[max(0, start - width) : start]
    return _CLAUSE_END.split(before)[-1]


def _cue_words(clause: str, words: int = 7) -> str:
    """The last words of the clause before a quotation: what the person reads
    back when the constraint is not met ("sans le mot « ... »")."""
    segment = clause.rsplit(",", 1)[-1]
    if len(segment.split()) < 2:
        segment = clause
    parts = segment.split(" ")
    return " ".join(parts[-(words + 1) :]) if len(parts) > words + 1 else segment


def _quoted_texts(text: str) -> Iterator[tuple[int, int, str]]:
    for match in _QUOTED.finditer(text):
        value = next(group for group in match.groups() if group is not None)
        if value.strip():
            yield match.start(), match.end(), value.strip()


def read(text: str, turn: int) -> list[Constraint]:
    """The constraints code reads in one message of the person."""
    from packages.deferred_intent import creation_deferred
    from services.orchestrator.only_request import asks_only

    text = text[:6000]
    found: list[Constraint] = []
    file_request = bool(_FILE_REQUEST.search(fold(text)))

    def add(
        kind: str,
        quote: str,
        *,
        text: str = "",
        count: int | None = None,
        relation: str = "exact",
    ) -> None:
        quote = " ".join(quote.split())[:240]
        found.append(
            Constraint(
                kind=kind,
                scope="answer",
                quote=quote,
                turn=turn,
                text=text,
                count=count,
                relation=relation,
            )
        )

    previous_end = 0
    for start, end, value in _quoted_texts(text):
        clause = _clause_before(text, start)
        context = fold(clause)
        quote = (_cue_words(clause) + text[start:end]).strip()
        names_answer = re.search(rf"\b{_ANSWER}\b", context) is not None
        if _STARTS.search(context) and (names_answer or not file_request):
            add("starts_with", quote, text=value)
        elif _ENDS.search(context) and (names_answer or not file_request):
            add("ends_with", quote, text=value)
        elif _UNDER.search(context):
            add("after_file", quote, text=value)
        elif _EXCLUDES.search(context) or (
            # "sans les mots « a » ni « b »": the next quotation of the list.
            found
            and found[-1].kind == "excludes"
            and re.fullmatch(
                r"\s*(?:,|ni|ou|or|nor|oder|noch|o|ni|ne|e|et|and|und|y)\s*",
                fold(text[previous_end:start]) or " ",
            )
        ):
            add("excludes", quote, text=value)
        previous_end = end

    for sentence, folded in _sentences(text):
        for match in _SENTENCES.finditer(folded):
            before = folded[: match.start()]
            told = _TELL_BEFORE.search(before) is not None
            if file_request and not told:
                continue
            count = (
                int(match["n"]) if match["n"].isdigit() else _NUMBERS.get(match["n"])
            )
            if count is None or not 1 <= count <= 30:
                continue
            after = folded[match.end() :][:24]
            relation = (
                "max"
                if match["most"] or _AFTER_MOST.match(after)
                else "min"
                if match["least"] or _AFTER_LEAST.match(after)
                else "exact"
            )
            add("sentences", sentence, count=count, relation=relation)
            break
        if _NO_INTRO.search(folded):
            add("no_intro", sentence)
        if _NO_CHANGE.search(folded):
            add("no_new_file", sentence)
    if asks_only(text):
        add("only", _only_sentence(text))
    if creation_deferred(text) and not any(c.kind == "no_new_file" for c in found):
        add("no_new_file", _deferral_sentence(text))
    return found


def _only_sentence(text: str) -> str:
    from services.orchestrator.only_request import asks_only

    for sentence, _ in _sentences(text):
        if asks_only(sentence):
            return sentence
    return text[:240]


def _deferral_sentence(text: str) -> str:
    from packages.deferred_intent import creation_deferred

    for sentence, _ in _sentences(text):
        if creation_deferred(sentence):
            return sentence
    return text[:240]


# What makes a turn worth holding its answer for the model reading: quoted
# text, or words about the form of the reply.
_FORM_HINT = re.compile(
    r"\b(?:commence\w*+|termine\w*+|phrases?|puces?|cases?\sa\scocher|liste|ordre|"
    r"dans\scet\sordre|mot\spour\smot|tel(?:le)?\squel(?:le)?|exactement|uniquement|"
    r"seulement|sans\s(?:titre|texte|intro\w*+|lien)|"
    r"start\w*+\swith|begin\w*+\swith|end\w*+\swith|sentences?|bullets?|bullet\spoints?|"
    r"checkbox\w*+|numbered|in\sthis\sorder|word\sfor\sword|verbatim|exactly|only|"
    r"beginn\w*+\smit|satz|satze|saetze|stichpunkt\w*+|aufzahlung\w*+|aufzaehlung\w*+|"
    r"reihenfolge|wortlich|woertlich|genau|nur|"
    r"empie\w*+\scon|frases?|vinetas?|casillas?|orden|literal\w*+|exactamente|solo|"
    r"inizi\w*+\scon|frasi|elenco\spuntato|caselle|ordine|testualmente|esattamente|"
    r"soltanto|"
    # Typography and layout: italic, bold, numbers, commas, lines, paragraphs,
    # tables and columns, titles, word counts.
    r"italic\w*+|italique\w*+|kursiv\w*+|cursiva\w*+|corsiv\w*+|bold|gras|fett\w*+|"
    r"negrita\w*+|grassetto|numbers?|numeros?|nummer\w*+|numeri|commas?|virgules?|"
    r"komma\w*+|comas?|virgol\w*+|lines?|lignes?|zeilen?|lineas?|righe?|paragraphs?|"
    r"paragraphes?|absatz\w*+|absatze|parrafos?|paragraf\w*+|tables?|tableaux?|tabellen?|"
    r"tablas?|tabell\w*+|columns?|colonnes?|spalten?|columnas?|colonn\w*+|titles?|"
    r"titres?|titel|titulos?|titol\w*+|words?|mots|worter|woerter|palabras|parole|"
    r"tutoie\w*+|vouvoie\w*+|duzen|siezen|tutea\w*+|usted|dando\sdel\slei)\b"
)


def worth_reading(text: str) -> bool:
    """Whether the model reading may find form constraints code did not."""
    return bool(_QUOTED.search(text)) or bool(_FORM_HINT.search(fold(text)))
