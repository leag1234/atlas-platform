"""A request for the answer alone: nothing around the requested output.

"Show me only the two console lines", "just tell me what remains to check",
"nothing else": the person asks for exactly one deliverable. A turn adds its
own furniture around it (a lead sentence, links to the saved file, the plan
card, a closing remark), each written by a different part of the pipeline, so
the request is honoured once, on the finished answer, by code.

The restriction is read from the request by phrase classes in six languages
(French, English, German, Spanish, Italian, Portuguese):

* a plain "nothing else" / "no other text" / "no commentary";
* a verb of showing or telling followed closely by "only" / "just";
* "only" followed by a short noun phrase naming the output (lines, result).

An "only" that limits a change ("change only the title") is not a limit of
the answer and does not match. A picture asked for alone is handled by
pictures/bare.py, which shares these phrases.
"""

from __future__ import annotations

import re
import unicodedata


def folded(text: str) -> str:
    """Lower case, no accents, apostrophes and hyphens as spaces."""
    text = text.replace("’", "'").replace("ʼ", "'")
    plain = "".join(
        character
        for character in unicodedata.normalize("NFKD", text.casefold())
        if not unicodedata.combining(character)
    )
    return " ".join(re.sub(r"['\-]", " ", plain).split())


_ONLY = (
    r"(?:seulement|uniquement|juste|rien que|only|just|nur|solo|solamente|soltanto|"
    r"unicamente|apenas|somente)"
)
_SHOW = (
    r"(?:montre|montres|montrez|affiche|affichez|donne|donnez|dis|dites|ecris|ecrivez|"
    r"reponds|repondez|indique|liste|renvoie|renvoyez|retourne|"
    r"show|display|give|tell|print|write|answer|reply|list|return|say|output|post|"
    r"zeig|zeige|zeigen|gib|gebe|nenn|nenne|sag|sage|schreib|schreibe|antworte|"
    r"gibt|liefer|liefere|"
    r"muestra|muestrame|dame|dime|escribe|responde|indica|devuelve|"
    r"mostra|mostrami|dammi|dimmi|scrivi|rispondi|indica|"
    r"mostre|diga|escreva|responda|me da|devolva)"
)
# What follows "only" and is not a limit of the answer: a condition.
_NOT_CONDITION = (
    r"(?!\s+(?:si|if|when|quand|lorsque|wenn|falls|cuando|se|quando|caso)\b)"
)
_NOTHING_ELSE = (
    r"\brien d autre\b|\brien de plus\b|\brien en plus\b|\bpas autre chose\b"
    r"|\bnothing else\b|\bnothing more\b|\bnothing extra\b|\bno extras?\b"
    r"|\bnichts anderes\b|\bnichts weiter\b|\bnichts sonst\b|\bnichts mehr\b"
    r"|\bnada mas\b|\bnada de mas\b|\bnada otro\b|\bnada adicional\b"
    r"|\bniente altro\b|\bnient altro\b|\bsenza altro\b|\bnulla di piu\b"
    r"|\bnada mais\b|\bmais nada\b|\bnada alem\b"
)
_NO_EXTRA = (
    r"\b(?:pas d|aucun|sans) (?:autre|autres|supplementaire)s? "
    r"(?:texte|commentaire|phrase|mot|explication|lien)s?\b"
    r"|\b(?:pas de|sans|aucun) (?:commentaire|explication|phrase|introduction|conclusion|"
    r"remarque|lien|note)s?\b"
    r"|\bno (?:other|extra|additional) (?:text|comment|commentary|sentence|word|"
    r"explanation|link)s?\b"
    r"|\b(?:no|without) (?:any )?(?:commentary|comments?|explanations?|preamble|"
    r"remarks?|notes?|links?)\b"
    r"|\bkein(?:e|en)? (?:weiteren?|anderen?|zusaetzlichen?)? ?"
    r"(?:text|kommentar|erklaerung|satz|link|anmerkung)e?s?\b"
    r"|\bohne (?:weiteren? |zusaetzlichen? )?(?:text|kommentar|erklaerung|satz|links?|"
    r"anmerkung|einleitung)e?n?\b"
    r"|\b(?:sin|ningun) (?:otro |mas |ningun )?(?:texto|comentario|explicacion|frase|"
    r"enlace|nota)s?\b"
    r"|\b(?:senza|nessun|nessuna) (?:altro |altra |ulteriore )?(?:testo|commento|"
    r"spiegazione|frase|link|nota)i?\b"
    r"|\b(?:sem|nenhum|nenhuma) (?:outro |outra |mais )?(?:texto|comentario|"
    r"explicacao|frase|link|nota)s?\b"
)
_OUTPUT = (
    r"(?:lignes?|resultats?|reponses?|sorties?|phrases?|mots?|valeurs?|chiffres?|"
    r"nombres?|ce qu il (?:reste|manque)|ce qui (?:reste|manque)|"
    r"lines?|results?|answers?|outputs?|sentences?|words?|values?|numbers?|what (?:is )?"
    r"(?:left|remains?|missing)|"
    r"zeilen?|ergebnis(?:se)?|antworten?|ausgaben?|saetze?|woerter|werte?|zahlen|"
    r"lineas?|resultados?|respuestas?|salidas?|frases?|palabras?|valores?|numeros?|"
    r"righe?|risultati?|risposte?|uscite|parole|valori|numeri|"
    r"linhas?|respostas?|saidas?|palavras?|numeros?)"
)
_DETERMINER = (
    r"(?:l|la|le|les|des|un|une|the|these|those|a|an|die|das|den|der|ein|eine|el|los|"
    r"las|un|una|il|lo|i|gli|le|o|os|as|ce|cette|ces|this)"
)
_COUNT = r"(?:\d+|deux|trois|quatre|cinq|two|three|four|five|zwei|drei|vier|funf|fuenf|dos|tres|due|tre|duas)"

# "Nothing else" and "no other text", without a verb: shared with
# pictures/bare.py, where it limits the answer around a picture.
PLAIN = re.compile(_NOTHING_ELSE + "|" + _NO_EXTRA)

RESTRICTION = re.compile(
    _NOTHING_ELSE
    + "|"
    + _NO_EXTRA
    # "show me only ...": a verb of showing, up to two words, then "only".
    + rf"|\b{_SHOW}\b(?:\s+\w+){{0,2}}\s+{_ONLY}\b{_NOT_CONDITION}"
    # "only the two lines", "just the result": the output named after "only".
    + rf"|\b{_ONLY}\s+(?:{_DETERMINER}\s+)?(?:{_COUNT}\s+)?(?:\w+\s+)?{_OUTPUT}\b"
)


def asks_only(request_text: str) -> bool:
    """True when the request asks for its deliverable with nothing around it."""
    return bool(RESTRICTION.search(folded(request_text[:4000])))


_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
_LINK = re.compile(r"!?\[[^\]\n]*\]\([^)\s]*\)")
_PICTURE = re.compile(r"!\[")
_QUOTE = re.compile(r"^\s*>")


def _blocks(answer: str) -> list[str]:
    """The paragraphs of an answer; a fenced code block stays in one piece."""
    blocks: list[str] = []
    current: list[str] = []
    fence = ""
    for line in answer.splitlines():
        marker = _FENCE.match(line)
        if marker:
            fence = (
                ""
                if fence and marker.group(1)[0] == fence[0]
                else fence or marker.group(1)
            )
        if not fence and not line.strip() and not marker:
            if current:
                blocks.append("\n".join(current))
                current = []
            continue
        current.append(line)
    if current:
        blocks.append("\n".join(current))
    return blocks


def _only_links(block: str) -> bool:
    """A paragraph made of links and the few words that introduce them."""
    if _PICTURE.search(block) or _FENCE.match(block) or _QUOTE.match(block):
        return False
    if not _LINK.search(block):
        return False
    rest = _LINK.sub("", block)
    rest = re.sub(r"[·|\-–—:*_`]+", " ", rest)
    words = re.findall(r"\w+", rest)
    return len(words) <= 6 and not re.search(r"[.!?]\s*$", rest.strip())


_STEP = re.compile(r"^\s*[-*]\s+[✓⊘○✗✔☐☑]")


def _steps(block: str) -> bool:
    """The ticked steps of the checked plan card (work_plan.py)."""
    lines = [line for line in block.splitlines() if line.strip()]
    return bool(lines) and all(_STEP.match(line) for line in lines)


def _without_plan(blocks: list[str]) -> list[str]:
    """The blocks without the plan card: its bold title and its ticked steps."""
    kept: list[str] = []
    for index, block in enumerate(blocks):
        following = blocks[index + 1] if index + 1 < len(blocks) else ""
        title = (
            block.lstrip().startswith("**")
            and "\n" not in block.strip()
            and _steps(following)
        )
        if title or _steps(block):
            continue
        kept.append(block)
    return kept


def restrict(answer: str) -> str:
    """The answer reduced to its deliverable.

    Links to the saved file and the plan card go. When the deliverable is a
    code block or a quotation, the sentence that introduces it goes, and after
    a quotation the remark on it. Never returns an empty answer.
    """
    blocks = _blocks(answer)
    kept = [block for block in _without_plan(blocks) if not _only_links(block)]
    if not kept:
        return answer
    fenced = [i for i, block in enumerate(kept) if _FENCE.match(block)]
    quoted = [i for i, block in enumerate(kept) if _QUOTE.match(block)]
    if fenced:
        first = fenced[0]
        # The lead only introduces the block; what follows a code block may
        # explain a failure and is kept.
        if first > 0 and kept[first - 1].rstrip().endswith(":"):
            kept = kept[: first - 1] + kept[first:]
    elif quoted:
        first, last = quoted[0], quoted[-1]
        before = kept[:first]
        if before and before[-1].rstrip().endswith(":"):
            before = before[:-1]
        # A remark after a quotation is about the quotation, not part of it.
        kept = before + [
            re.sub(r"(?m)^\s*>\s?", "", block) for block in kept[first : last + 1]
        ]
    return "\n\n".join(kept).strip() or answer
