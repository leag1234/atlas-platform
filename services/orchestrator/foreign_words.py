"""A common function word of another language inside an answer's prose.

An open model writing in one language can let one small word of another slip
into a sentence: "le message de fin avec le score apparaît junto au bouton
« Rejouer »" in a French app answer (staging -177), "junto" being Spanish for
"next to". The reader sees a defect the model never meant.

The check is code, applied to the stream and to the stored answer alike, like
stray_script. For each answer language with a table (en, fr, de, es, it), a
small list of unambiguous function words of the other UI languages and of
Portuguese (adverbs, conjunctions, one preposition phrase: también, porque,
anche, auch, weil, também, because...) is replaced by the answer language's
equivalent. A word that is legitimate in the answer language is never listed
(LEGITIMATE: French "perché" is a participle, Italian "pero" a pear tree,
German "also" a German word, English "deja vu" a loan), and Italian "perche"
is not listed at all, since it means both "why" and "because".

A word is left as written when any of these holds, so that only a slip inside
a sentence of the answer language is touched:

- it sits in code (fenced or inline), quotation marks, parentheses, brackets
  (link text), emphasis, a URL, or the sources block;
- the person used it in the conversation (a language lesson, their own words);
- its context (the sentence up to it and the five words after it) names a
  language or speaks of words ("en espagnol", "the word");
- that context has fewer than two distinctive words of the answer language, or
  two distinctive words of the word's own language (a foreign clause);
- it is capitalised inside a sentence (a name: "Sturm und Drang", "Junto"), or
  stands next to a capitalised word, or next to another listed word;
- it opens a sentence and is followed neither by a comma nor by a word of the
  answer language ("Porque te vas" is a title).

Only counts are logged, never the text.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Awaitable, Callable, Iterable
import json
import logging
import re
import unicodedata

# Per answer language: the foreign phrase (lower case) and its replacement,
# with the language the phrase comes from. Longer phrases are matched first.
_TABLES: dict[str, dict[str, tuple[str, str]]] = {
    "fr": {
        "junto aux": ("es", "à côté des"),
        "junto au": ("es", "à côté du"),
        "junto à la": ("es", "à côté de la"),
        "junto à": ("es", "à côté de"),
        "junto al": ("es", "à côté du"),
        "junto a la": ("es", "à côté de la"),
        "junto a": ("es", "à côté de"),
        "junto de": ("es", "à côté de"),
        "junto con": ("es", "avec"),
        "junto": ("es", "à côté"),
        "también": ("es", "aussi"),
        "porque": ("es", "parce que"),
        "aunque": ("es", "même si"),
        "todavía": ("es", "encore"),
        "además": ("es", "de plus"),
        "sin embargo": ("es", "cependant"),
        "pero": ("es", "mais"),
        "anche": ("it", "aussi"),
        "quindi": ("it", "donc"),
        "tuttavia": ("it", "cependant"),
        "però": ("it", "mais"),
        "inoltre": ("it", "de plus"),
        "também": ("pt", "aussi"),
        "porém": ("pt", "cependant"),
        "então": ("pt", "alors"),
        "auch": ("de", "aussi"),
        "weil": ("de", "parce que"),
        "obwohl": ("de", "même si"),
        "aber": ("de", "mais"),
        "sondern": ("de", "mais"),
        "because": ("en", "parce que"),
        "however": ("en", "cependant"),
        "although": ("en", "même si"),
    },
    "es": {
        "aussi": ("fr", "también"),
        "parce que": ("fr", "porque"),
        "avec": ("fr", "con"),
        "toujours": ("fr", "siempre"),
        "anche": ("it", "también"),
        "quindi": ("it", "por lo tanto"),
        "però": ("it", "pero"),
        "tuttavia": ("it", "sin embargo"),
        "inoltre": ("it", "además"),
        "também": ("pt", "también"),
        "porém": ("pt", "sin embargo"),
        "então": ("pt", "entonces"),
        "auch": ("de", "también"),
        "weil": ("de", "porque"),
        "obwohl": ("de", "aunque"),
        "aber": ("de", "pero"),
        "because": ("en", "porque"),
        "however": ("en", "sin embargo"),
        "although": ("en", "aunque"),
    },
    "it": {
        "también": ("es", "anche"),
        "porque": ("es", "perché"),
        "aunque": ("es", "anche se"),
        "todavía": ("es", "ancora"),
        "además": ("es", "inoltre"),
        "sin embargo": ("es", "tuttavia"),
        "junto a": ("es", "accanto a"),
        "junto al": ("es", "accanto al"),
        "aussi": ("fr", "anche"),
        "parce que": ("fr", "perché"),
        "avec": ("fr", "con"),
        "toujours": ("fr", "sempre"),
        "também": ("pt", "anche"),
        "porém": ("pt", "tuttavia"),
        "então": ("pt", "allora"),
        "auch": ("de", "anche"),
        "weil": ("de", "perché"),
        "obwohl": ("de", "sebbene"),
        "aber": ("de", "ma"),
        "because": ("en", "perché"),
        "however": ("en", "tuttavia"),
        "although": ("en", "sebbene"),
    },
    "de": {
        "también": ("es", "auch"),
        "porque": ("es", "weil"),
        "aunque": ("es", "obwohl"),
        "todavía": ("es", "noch"),
        "además": ("es", "außerdem"),
        "sin embargo": ("es", "jedoch"),
        "pero": ("es", "aber"),
        "aussi": ("fr", "auch"),
        "parce que": ("fr", "weil"),
        "avec": ("fr", "mit"),
        "toujours": ("fr", "immer"),
        "anche": ("it", "auch"),
        "quindi": ("it", "daher"),
        "però": ("it", "aber"),
        "tuttavia": ("it", "jedoch"),
        "inoltre": ("it", "außerdem"),
        "também": ("pt", "auch"),
        "porém": ("pt", "jedoch"),
        "então": ("pt", "dann"),
        "because": ("en", "weil"),
        "however": ("en", "jedoch"),
        "although": ("en", "obwohl"),
    },
    "en": {
        "también": ("es", "also"),
        "porque": ("es", "because"),
        "aunque": ("es", "although"),
        "todavía": ("es", "still"),
        "además": ("es", "moreover"),
        "sin embargo": ("es", "however"),
        "junto a": ("es", "next to"),
        "junto al": ("es", "next to the"),
        "junto con": ("es", "together with"),
        "aussi": ("fr", "also"),
        "parce que": ("fr", "because"),
        "toujours": ("fr", "always"),
        "anche": ("it", "also"),
        "quindi": ("it", "so"),
        "però": ("it", "but"),
        "tuttavia": ("it", "however"),
        "inoltre": ("it", "moreover"),
        "também": ("pt", "also"),
        "porém": ("pt", "however"),
        "então": ("pt", "then"),
        "auch": ("de", "also"),
        "weil": ("de", "because"),
        "obwohl": ("de", "although"),
    },
}

# Words of another language that are also words of the answer language: never
# listed in its table (tests/test_foreign_words.py checks it).
LEGITIMATE: dict[str, frozenset[str]] = {
    "fr": frozenset({"perché", "con"}),
    "es": frozenset({"con", "come", "una"}),
    "it": frozenset({"pero", "con", "come", "una"}),
    "de": frozenset({"also", "so", "die", "will", "war"}),
    "en": frozenset({"also", "die", "will", "war", "déjà"}),
}

# Distinctive words of each language: two of the answer language say that a
# sentence is written in it; two of the word's own language say the sentence
# holds a clause of that language. None of them is common in another of these
# languages (Italian "il", "le", "una" and Spanish "es", "son" are left out).
_ANCHORS: dict[str, frozenset[str]] = {
    "fr": frozenset(
        "les des du et est une au aux pour qui dans sur avec elle ne pas ce cette "
        "sont très à vous nous leur être été".split()
    ),
    "de": frozenset(
        "der das und ist nicht mit für auf ein eine einen dem sich zu von wird sind "
        "bei wir ihr".split()
    ),
    "es": frozenset("el los las y está para por muy sus pero también".split()),
    "it": frozenset("gli è per della che non sono alla nel degli questo anche".split()),
    "en": frozenset("the and is are with of this that for you it was be".split()),
    "pt": frozenset("não uma com os são você ao".split()),
}
# Words that may follow a sentence-opening listed word in a sentence of the
# answer language (pronouns and articles).
_FOLLOWERS: dict[str, frozenset[str]] = {
    "fr": frozenset(
        "il ils elle elles on je tu nous vous le la les l c ça cela".split()
    ),
    "de": frozenset("es er sie wir ich du ihr der die das man".split()),
    "es": frozenset("el la los las yo tú usted nosotros se lo".split()),
    "it": frozenset("il lo la gli le io tu lui lei noi si".split()),
    "en": frozenset("it he she we i you they the this that there".split()),
}
# A sentence that names a language or speaks of words is about language.
_ABOUT_LANGUAGE: dict[str, re.Pattern[str]] = {
    "fr": re.compile(
        r"\b(?:espagnol|castillan|italien|allemand|portugais|anglais|langue|mot|mots|"
        r"expression|traduction|traduit|signifie|veut dire)\b",
        re.I,
    ),
    "de": re.compile(
        r"\b(?:spanisch\w*|italienisch\w*|portugiesisch\w*|englisch\w*|"
        r"französisch\w*|sprache|wort|wörter|ausdruck|übersetzung|bedeutet|heißt)\b",
        re.I,
    ),
    "es": re.compile(
        r"\b(?:italiano|alemán|portugués|inglés|francés|idioma|lengua|palabra|"
        r"palabras|expresión|traducción|significa|se dice)\b",
        re.I,
    ),
    "it": re.compile(
        r"\b(?:spagnolo|tedesco|portoghese|inglese|francese|lingua|parola|parole|"
        r"espressione|traduzione|significa|si dice)\b",
        re.I,
    ),
    "en": re.compile(
        r"\b(?:spanish|italian|german|portuguese|french|language|word|words|"
        r"phrase|expression|translation|translates|means)\b",
        re.I,
    ),
}

# Spans that are not the answer's own prose.
_PROTECTED = re.compile(
    r"<details.*\Z"
    r"|```.*?(?:```|\Z)"
    r"|`[^`\n]*(?:`|$)"
    r"|«[^»\n]*(?:»|$)"
    r"|“[^”\n]*(?:”|$)"
    r"|„[^“”\n]*(?:[“”]|$)"
    r'|"[^"\n]*(?:"|$)'
    r"|\([^)\n]*(?:\)|$)"
    r"|\[[^\]\n]*(?:\]|$)"
    r"|\*{1,3}(?=\S)[^*\n]*(?:\*|$)"
    r"|(?<!\w)_(?=\S)[^_\n]*(?:_|$)"
    r"|\b(?:https?://|www\.)\S+",
    re.S | re.M,
)
_SENTENCE_END = re.compile(r"[.!?…]+(?=\s)|\n")
_WORD = re.compile(r"\w+")


def _fold(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text.casefold())
    plain = "".join(
        c if c.isalnum() else " " for c in plain if not unicodedata.combining(c)
    )
    return " ".join(plain.split())


def _pattern(table: dict[str, tuple[str, str]]) -> re.Pattern[str]:
    phrases = sorted(table, key=len, reverse=True)
    body = "|".join(re.escape(p).replace(r"\ ", r"\s+") for p in phrases)
    return re.compile(r"(?<![\w'’-])(?:" + body + r")(?![\w-])", re.I)


_PATTERNS = {language: _pattern(table) for language, table in _TABLES.items()}
_HEADS = {
    language: frozenset(phrase.split()[0] for phrase in table)
    for language, table in _TABLES.items()
}


def active(language: str) -> bool:
    return language in _TABLES


def own_words(texts: Iterable[str]) -> str:
    """The person's messages, folded: a listed word they wrote is theirs."""
    return " " + " ".join(_fold(str(text)) for text in texts) + " "


def _sentence(text: str, start: int, end: int) -> tuple[int, int]:
    begin = 0
    for found in _SENTENCE_END.finditer(text, 0, start):
        begin = found.end()
    after = _SENTENCE_END.search(text, end)
    return begin, after.start() if after else len(text)


def _words(text: str) -> list[str]:
    return [w.casefold() for w in _WORD.findall(text)]


def _elided(replacement: str, rest: str, language: str) -> tuple[str, int]:
    """French contractions after a replacement ("parce qu'il", "du", "s'il"):
    returns the replacement and how many characters of ``rest`` it
    absorbs."""
    if language != "fr":
        return replacement, 0
    following = re.match(r"\s+(\w+)", rest)
    if not following:
        return replacement, 0
    word = following.group(1)
    lower = word.casefold()
    if replacement.endswith(" de") or replacement == "de":
        if lower in {"le", "les"}:
            return replacement[:-2] + (
                "du" if lower == "le" else "des"
            ), following.end()
    if re.search(r"(?:^|\s)(?:que|de)$", replacement) and re.match(
        r"[aeiouhàâéèêîôû]", lower
    ):
        return replacement[:-1] + "'" + word, following.end()
    if replacement.endswith(" si") and lower in {"il", "ils"}:
        return replacement[:-1] + "'" + word, following.end()
    return replacement, 0


def repair(
    text: str, language: str, own: str = " ", start: int = 0
) -> tuple[str, Counter[str]]:
    """``text`` with the listed foreign words of its prose replaced, from
    ``start`` on (the text before is context only), and the counts."""
    counts: Counter[str] = Counter()
    pattern = _PATTERNS.get(language)
    if pattern is None or not text:
        return text, counts
    table = _TABLES[language]
    protected = [(m.start(), m.end()) for m in _PROTECTED.finditer(text)]
    out: list[str] = []
    cursor = 0
    for match in pattern.finditer(text, start):
        if match.start() < cursor:
            continue
        phrase = " ".join(match.group(0).casefold().split())
        source, replacement = table[phrase]
        reason = _kept(text, match, language, source, own, protected)
        if reason:
            counts["kept_" + reason] += 1
            continue
        found = match.group(0)
        begin, _ = _sentence(text, match.start(), match.end())
        opens = not text[begin : match.start()].strip(" \t*-#>")
        replacement, absorbed = _elided(replacement, text[match.end() :], language)
        if opens and found[:1].isupper():
            replacement = replacement[:1].upper() + replacement[1:]
        out.append(text[cursor : match.start()])
        out.append(replacement)
        cursor = match.end() + absorbed
        counts["replaced"] += 1
        counts[source] += 1
    out.append(text[cursor:])
    return "".join(out), counts


# Words after a listed phrase that take part in its decision: the stream
# holds a listed word only until they arrive (or the sentence ends), so the
# rest of the answer is never held (first-content latency, test_streaming_network).
LOOKAHEAD_WORDS = 5
_LONGEST_PHRASE = max(
    len(phrase.split()) for table in _TABLES.values() for phrase in table
)
_AFTER = re.compile(r"(?:\W*\w+){0,%d}" % LOOKAHEAD_WORDS)


def _window(after: str) -> str:
    """The words after a listed phrase that its decision reads."""
    found = _AFTER.match(after)
    return found.group(0) if found else ""


def _kept(
    text: str,
    match: re.Match[str],
    language: str,
    source: str,
    own: str,
    protected: list[tuple[int, int]],
) -> str:
    """Why a listed word stays as written, or ""."""
    if any(a <= match.start() < b for a, b in protected):
        return "protected"
    found = match.group(0)
    if " " + _fold(found) + " " in own:
        return "own_words"
    begin, end = _sentence(text, match.start(), match.end())
    before = text[begin : match.start()]
    after = _window(text[match.end() : end])
    if _ABOUT_LANGUAGE[language].search(before + " " + after):
        return "about_language"
    context = _words(before) + _words(after)
    if sum(w in _ANCHORS[language] for w in set(context)) < 2:
        return "not_answer_language"
    if sum(w in _ANCHORS.get(source, frozenset()) for w in set(context)) >= 2:
        return "foreign_clause"
    opens = not before.strip(" \t*-#>")
    if found[:1].isupper() and not opens:
        return "name"
    previous = _WORD.findall(before)[-1:]
    following = re.match(r"\s*(\W?)\s*(\w*)", after)
    following_mark = following.group(1) if following else ""
    following_word = following.group(2) if following else ""
    heads = _HEADS[language]
    for word in (*previous, following_word):
        if word and (word.casefold() in heads):
            return "foreign_run"
    if previous and previous[0][:1].isupper() and len(_words(before)) > 1:
        return "name"
    if following_word[:1].isupper() and not following_mark:
        return "name"
    if (
        opens
        and following_mark != ","
        and (following_mark or following_word.casefold() not in _FOLLOWERS[language])
    ):
        return "title"
    return ""


def clean(
    answer: str, language: str, person: Iterable[str] = ()
) -> tuple[str, dict[str, int]]:
    """The complete answer with its listed foreign words replaced, and the counts."""
    text, counts = repair(answer, language, own_words(person))
    return text, dict(counts)


def log(counts: dict[str, int], task_type: str) -> None:
    """Counts only: the answer never reaches the log."""
    if counts:
        logging.getLogger(__name__).warning(
            "foreign_words %s",
            json.dumps({**counts, "task_type": task_type}, sort_keys=True),
        )


class Rewriter:
    """Streams answer text through ``repair``.

    Text goes out as it comes. Held back: the last word only when it may still
    grow into a listed one, and a listed word only until the few words after it
    that decide its case have arrived (LOOKAHEAD_WORDS) or its sentence ends.
    The first content of the turn is never held.
    """

    def __init__(self, language: str, person: Iterable[str] = ()) -> None:
        self.language = language
        self.active = active(language)
        self.own = own_words(person)
        self.sent = ""
        self.held = ""
        self.started = False
        self.counts: Counter[str] = Counter()

    def _emit(self, ready: str) -> str:
        if not ready:
            return ""
        heads = _HEADS[self.language]
        if not any(w.casefold() in heads for w in _WORD.findall(ready)):
            self.sent += ready
            return ready
        start = len(self.sent)
        text, counts = repair(self.sent + ready, self.language, self.own, start)
        self.counts.update(counts)
        out = text[start:]
        self.sent = text
        return out

    def _decided(self, position: int) -> bool:
        """Whether the text after a listed word at ``position`` settles it."""
        after = self.held[position:]
        stop = _SENTENCE_END.search(after)
        if stop:
            return True
        complete = [m for m in _WORD.finditer(after) if m.end() < len(after)]
        return len(complete) >= _LONGEST_PHRASE - 1 + LOOKAHEAD_WORDS

    def _cut(self) -> int:
        heads = _HEADS[self.language]
        held = self.held
        for found in _WORD.finditer(held):
            word = found.group(0).casefold()
            if found.end() == len(held):
                # The last word may still grow: held only if it may become
                # a listed one.
                if any(head.startswith(word) for head in heads):
                    return found.start()
                return len(held)
            if word in heads and not self._decided(found.end()):
                return found.start()
        return len(held)

    def feed(self, text: str) -> str:
        if not self.active:
            return text
        if not self.started:
            if not text:
                return ""
            self.started = True
            self.sent += text
            return text
        self.held += text
        cut = self._cut()
        if cut <= 0:
            return ""
        ready, self.held = self.held[:cut], self.held[cut:]
        return self._emit(ready)

    def finish(self) -> str:
        if not self.active:
            return ""
        ready, self.held = self.held, ""
        return self._emit(ready)

    def report(self) -> dict[str, int]:
        return dict(self.counts)


Sink = Callable[[dict[str, object]], Awaitable[None]]


class Gate:
    """Streams answer text through a Rewriter; other events keep their order."""

    def __init__(self, sink: Sink, rewriter: Rewriter) -> None:
        self.sink = sink
        self.rewriter = rewriter

    async def __call__(self, event: dict[str, object]) -> None:
        delta = event.get("delta")
        if (
            len(event) == 1
            and isinstance(delta, dict)
            and len(delta) == 1
            and isinstance(delta.get("content"), str)
        ):
            text = self.rewriter.feed(str(delta["content"]))
            if text:
                await self.sink({"delta": {"content": text}})
            return
        await self.flush()
        await self.sink(event)

    async def flush(self) -> None:
        text = self.rewriter.finish()
        if text:
            await self.sink({"delta": {"content": text}})
