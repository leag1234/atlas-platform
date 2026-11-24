"""Code-side link discovery and bounded shopping answer policy (D7)."""

from packages import attached_blocks, pasted_text
import re
import unicodedata
from collections.abc import Sequence

# A message this long whose middle is made of long paragraphs carries a pasted
# document (an article, a source to check, passages): the routing words are read
# in the person's own lines around it, never in the document (pilot 2026-11-17).
# Sizes are UTF-8 bytes, so that a dense script (Japanese, Chinese) counts as
# much text as its Latin translation.
DOCUMENT_BYTES = 1000
DOCUMENT_PARAGRAPH_BYTES = 200


def own_request(text: str) -> str:
    """The person's own lines of a message, without a long document pasted in it.

    The document is the block from the first to the last long paragraph. When no
    short line is left around it, the whole message is the request.
    """
    if len(text.encode()) < DOCUMENT_BYTES:
        return text
    paragraphs = [p for p in re.split(r"\n[ \t]*\n", text) if p.strip()]
    long = [
        index
        for index, paragraph in enumerate(paragraphs)
        if len(paragraph.encode()) > DOCUMENT_PARAGRAPH_BYTES
    ]
    if not long:
        return text
    kept = paragraphs[: long[0]] + paragraphs[long[-1] + 1 :]
    return "\n\n".join(kept) if kept else text


def own_words(text: str) -> str:
    """The person's own words, for the words that classify a request.

    As own_request, from a shorter message on (a pasted passage of a few hundred
    bytes is still a pasted text), without attached file blocks, and also when a
    single line break ("Summarize the following:" then the text) or a colon on
    the same line ("Résume ce texte : ...") is all that separates the request
    from the pasted text (owner ruling 2026-11-17: a pasted text never
    classifies the request). A text of short paragraphs or lines, a fenced or
    quoted text, and a text the request follows ("TL;DR this") are pasted
    texts too (staging 2026-11-19).
    """
    return split_pasted(text)[0]


def pasted_parts(text: str) -> list[str]:
    """What own_words leaves out: the pasted text and the attached file blocks."""
    return split_pasted(text)[1]


def split_pasted(text: str) -> tuple[str, list[str]]:
    """The person's own words, and the pasted parts around which they are read.

    Attached file blocks are pasted parts; the rest is told apart by its
    layout (packages/pasted_text.py, shared with the answer language).
    """
    pasted = [found[0] for found in attached_blocks.BLOCKS_ANY_CASE.finditer(text)]
    text = attached_blocks.BLOCKS_ANY_CASE.sub("", text)
    own, parts = pasted_text.split(text)
    return own, [*pasted, *parts]


# "Link" and "lien" also name a relation ("the link between sleep and memory",
# "le lien entre"), and English "links" is also a verb ("the tunnel links two
# countries", "the facts it links"). Neither asks for web pages.
_RELATION = re.compile(
    r"\b(?:liens?|links?)\s+(?:\w+\s+){0,2}?(?:between|entre|zwischen|tra|fra)\b",
    re.IGNORECASE,
)
_VERB = re.compile(
    r"(?i:\b(?:it|that|which|this|who|he|she)\s+links\b)"
    r"|(?<=\w\s)links\s+(?:[A-Z]|(?:the|a|an|both|two|them|it|us)\b)"
)


def _asks_for_links(question: str) -> bool:
    plain = _VERB.sub(" ", _RELATION.sub(" ", question))
    return bool(re.search(r"(?i)\b(liens?|links?|urls?)\b", plain))


def link_request(question: str, *, prices: bool = False) -> bool:
    # Upload transport metadata is not a request for URLs or product prices.
    question = attached_blocks.BLOCKS_ANY_CASE.sub("", question)
    text = "".join(
        c
        for c in unicodedata.normalize("NFD", question.casefold())
        if not unicodedata.combining(c)
    )
    # "Cost the ..." asks us to calculate a budget, not retrieve a market price.
    if re.match(r"^(?:please\s+)?cost\s+(?:the|this|these|my|our|a|an)\b", text):
        return False
    if re.search(
        r"\b(without|no|sans|pas de|ohne|keine?)\s+(?:web\s+)?(?:links?|liens?|recherche|search|suche)\b"
        r"|\b(do not|don't|ne|nicht)\b.{0,40}\b(search|cherch\w*|such\w*)\b"
        r"|\b(internal|interne|confidential|confidentiel|vertraulich)\b",
        text,
    ):
        return False
    price_request = prices and bool(
        re.search(
            r"\b(prix|prices?|preis\w*|tarifs?|costs?|coute|coutent|kostet|kosten)\b"
            r"|\b(how much (?:is|does|for)|combien (?:ca )?(?:vaut|pour)|wie teuer)\b",
            text,
        )
    )
    if _asks_for_links(question):
        return True
    return price_request or bool(
        re.search(
            r"\b(shopping|acheter|buy|kaufen)\b"
            # The French "where is X" phrasing asks where X is, not where to buy it.
            r"|\b(ou|where|wo)\b(?!\s+s(?:e\s+|['’]\s*)trouv)"
            r".{0,35}\b(achet\w*|trouv\w*|buy|get|purchase|kauf\w*|bekomm\w*)\b"
            r"|\b(product|produit|produkt)\s*(pages?|seiten?)\b"
            r"|\b(pages?|fiches?)\s+(?:de\s+)?produit\w*\b"
            r"|\b(show|montre\w*|affiche\w*|zeig\w*)\b.{0,35}\b(pages?|seiten?)\b",
            text,
        )
    )


# A link Atlas gave in this conversation's answers: an app or drawing version,
# a saved project file, a file of the chat's own workspace.
_DELIVERED_LINK = re.compile(
    r"/api/atlas/artifacts/[0-9a-f]{32}/versions/\d{1,4}/(?:open|download)"
    r"|/api/atlas/orgs/[^\s()]{1,200}?/deliverables/[0-9a-f-]{36}/(?:download|preview)"
    r"|/api/v1/terminals/[^\s()/]{1,80}/files/serve/"
)
# What names such a delivery, in the five languages (accents removed): a file,
# a document, an app, a version, a file format.
_DELIVERY_NOUN = (
    r"(?:files?|fichiers?|dateien|datei|archivos?|documents?|dokumente?|documentos?|"
    r"document[oi]|apps?|applications?|anwendung(?:en)?|aplicacion(?:es)?|"
    r"applicazion[ei]|versions?|versione|tableurs?|spreadsheets?|tabellen?|"
    r"presentations?|prasentation(?:en)?|presentacion(?:es)?|presentazion[ei]|"
    r"rapports?|reports?|berichte?|informes?|relazion[ei]|html|pdf|docx|xlsx|pptx|"
    r"csv|svg)"
)
# A delivery named as the one already there: an article, a demonstrative, a
# possessive or "same" before it, at most two words between ("the same HTML
# file", "zur selben HTML-Datei", "el mismo archivo", "lo stesso file").
_OWN_DELIVERY = re.compile(
    r"\b(?:the|this|that|your|my|same|le|la|l'|ce|cet|cette|ton|ta|votre|mon|ma|"
    r"meme|der|die|das|den|dem|zur|zum|diese[rsmn]?|deine[rsmn]?|ihre[rsmn]?|"
    r"selbe[nrs]?|gleiche[nrs]?|el|este|esta|tu|su|mismo|misma|del|al|il|lo|"
    r"questo|questa|quel|quella|tuo|tua|stess[oa]|dello|della|allo|alla)"
    r"(?:\s+|(?<=')\s*)(?:[\w]+[\s-]+){0,2}?" + _DELIVERY_NOUN + r"\b"
    # "Version 2", "versión 3": a numbered version of the delivery.
    r"|\bversi(?:on|one)\s*\d"
)


def own_delivery(question: str, history: Sequence[str] = ()) -> bool:
    """The request is about what this conversation's answers delivered.

    Resend its link, restate its status, rename it, repeat it: the answer comes
    from the conversation and its stored deliverables, never from a mandatory
    web search. Class suite, staging 2026-11-24: "send the link to the same
    HTML file again" in a conversation that had delivered that file read
    "link" as a request for web pages; five searches ran, every engine was
    blocked, and the person got an empty reply. Applies only when an answer of
    the conversation holds a delivered link; web search stays offered.
    """
    if not any(_DELIVERED_LINK.search(turn) for turn in history):
        return False
    text = "".join(
        c
        for c in unicodedata.normalize("NFD", own_words(question).casefold())
        if not unicodedata.combining(c)
    ).replace("’", "'")
    return bool(_OWN_DELIVERY.search(text))


def page_request(question: str, history: Sequence[str] = ()) -> bool:
    question = own_words(question)
    if own_delivery(question, history):
        return False
    if link_request(question):
        return True
    # A price-only question inherits shopping intent only from the active thread.
    return (
        bool(history)
        and any(link_request(turn) for turn in history[-2:])
        and (
            link_request(question, prices=True)
            or bool(
                re.search(r"(?i)\b(et chez|what about|and those|und bei)\b", question)
            )
        )
    )


def product_queries(question: str, history: Sequence[str] = ()) -> tuple[str, ...]:
    # Identifiers come from the user's request or the latest recommendation, never
    # from a product catalogue embedded in routing code.
    context = question + "\n" + (history[-1] if history else "")
    context = re.sub(r"https?://\S+", "", context)
    products = list(
        dict.fromkeys(
            re.findall(
                r"\b[A-Z][a-zA-Z]+[ \t]+[A-Z]{1,4}(?:-[A-Z]{1,4})?[- ]?\d{1,4}[A-Za-z0-9-]*\b",
                context,
            )
        )
    )
    shopping = re.search(
        r"(?i)\b(thomann|woodbrass|boulanger|shop\w*|achet\w*|buy|kauf\w*)\b", question
    )
    if not products or not shopping:
        return (link_query(question, history),)
    # One query per model, targeted at multiple shops, stays within the existing
    # three-search quota even when three models were recommended.
    shops = "(site:thomann.de OR site:woodbrass.com OR site:boulanger.com)"
    return tuple(product + " " + shops for product in products[:3])


def link_query(question: str, history: Sequence[str] = ()) -> str:
    # History supplies the referent, never provenance. Keep user wording first.
    context = re.sub(r"https?://\S+", "", " ".join(history[-2:]))
    return (question + (" " + context if context else ""))[:500]


def fallback_terms(question: str, history: Sequence[str] = ()) -> str:
    """Give a concise query hint without echoing the complete request."""
    context = re.sub(r"https?://\S+", "", question + "\n" + "\n".join(history[-1:]))
    products = list(
        dict.fromkeys(
            re.findall(
                r"\b[A-Z][a-zA-Z]+[ \t]+[A-Z]{1,4}(?:-[A-Z]{1,4})?[- ]?\d{1,4}[A-Za-z0-9-]*\b",
                context,
            )
        )
    )
    if products:
        return ", ".join(products[:3])
    # No product name: the user's own words are the search terms (never an English placeholder).
    return re.sub(r"\s+", " ", re.sub(r"https?://\S+", "", question)).strip()[:200]


def instruction() -> str:
    return (
        "\nThis request requires verified links: use web_search and web_fetch to "
        "find and read the requested pages, using previous recommendations as context. "
        "For shopping, group by product and give the shop name and product page link, "
        "2 to 3 shops per recommended model. Search each product with shop site filters. "
        "A product URL returned by web_search is retrieved and can be linked even "
        "when the shop blocks web_fetch. Include a price only when explicitly read "
        "on that fetched page or in that URL's search snippet, naming the evidence "
        "source and 'price at the time of reading' in the user's language. "
        "Otherwise omit the price and say it could not be verified. Never invent "
        "links or prices. Never transfer a price between URLs. "
        "Use ordinary bullets, not shop headings or setext underlines. If retrieval "
        "fails, state that verified links could not be retrieved and give search terms.\n"
    )


# Why the links could not be retrieved, said in the user's language when the error carries no
# detail: a search limit must read as a limit, never as a retrieval failure (D5).
REASONS = {
    "daily_search_limit": {
        "fr": "Votre limite de recherches web est atteinte pour le moment.",
        "de": "Ihr Limit für Websuchen ist vorerst erreicht.",
        "en": "Your web search limit is reached for now.",
        "es": "Se alcanzó su límite de búsquedas web por ahora.",
        "it": "Il tuo limite di ricerche web è stato raggiunto per ora.",
    },
    "quota_exceeded": {
        "fr": "Le service de recherche web a atteint son quota.",
        "de": "Der Websuchdienst hat sein Kontingent erreicht.",
        "en": "The web search service has reached its quota.",
        "es": "El servicio de búsqueda web alcanzó su cuota.",
        "it": "Il servizio di ricerca web ha raggiunto la sua quota.",
    },
    "search_rate_limited": {
        "fr": "Le service de recherche web limite les requêtes : réessayez dans un moment.",
        "de": "Der Websuchdienst begrenzt die Anfragen: Versuchen Sie es gleich noch einmal.",
        "en": "The web search service is limiting requests: try again in a moment.",
        "es": "El servicio de búsqueda web limita las solicitudes: vuelva a intentarlo en un momento.",
        "it": "Il servizio di ricerca web limita le richieste: riprova tra un momento.",
    },
    "search_unavailable": {
        "fr": "La recherche web est indisponible pour le moment.",
        "de": "Die Websuche ist gerade nicht verfügbar.",
        "en": "Web search is unavailable right now.",
        "es": "La búsqueda web no está disponible en este momento.",
        "it": "La ricerca web non è disponibile in questo momento.",
    },
    "search_no_results": {
        "fr": "La recherche web n’a donné aucun résultat.",
        "de": "Die Websuche hat keine Ergebnisse geliefert.",
        "en": "The web search returned no results.",
        "es": "La búsqueda web no dio resultados.",
        "it": "La ricerca web non ha dato risultati.",
    },
}


def reason(code: str, language: str) -> str:
    texts = REASONS.get(code, {})
    return texts.get(language, texts.get("en", ""))


# A search that failed before any page was read ends the turn (the answer is
# never written from memory in its place, chat_pipeline.ChatTools): the person
# still reads a reply saying so, never an empty one (class suite, staging
# 2026-11-24: an error event with no text).
SEARCH_FAILED = {
    "fr": "Je n’ai pas pu traiter cette demande, car la recherche web qu’elle demande n’a pas abouti.",
    "de": "Ich konnte diese Anfrage nicht bearbeiten, weil die dafür nötige Websuche nicht gelungen ist.",
    "en": "I could not complete this request because the web search it needs did not succeed.",
    "es": "No pude completar esta solicitud porque la búsqueda web que necesita no tuvo éxito.",
    "it": "Non ho potuto completare questa richiesta perché la ricerca web necessaria non è riuscita.",
}
_PROVIDER_STATUS = re.compile(r"\bHTTP (\d{3})\b")
# The codes of a search that failed before any page was read (ChatTools.execute
# raises them): the search outcomes and the provider refusals.
SEARCH_FAILURES = frozenset(
    {
        "upstream_blocked",
        "upstream_timeout",
        "empty_results",
        "partial_results",
        "search_unavailable",
        "quota_exceeded",
        "search_rate_limited",
        "search_no_results",
        "daily_search_limit",
    }
)


def failed_links_reply(
    code: str, detail: str, question: str, history: Sequence[str], language: str
) -> str:
    """The reply of a link request whose search failed before any page was read.

    A limit or a quota is named as such (D5), with the provider's own words
    when the refusal carries them. The caveat of a search outcome (blocked,
    timed out, nothing found) is added after the answer by
    chat_pipeline.process, in the person's language: not repeated here.
    """
    from services.orchestrator.searxng import OUTCOMES

    terms = unavailable(fallback_terms(question, history), language)
    if code in OUTCOMES:
        return terms
    cause = detail.strip() or reason(code, language)
    return terms + ("\n\n" + cause if cause else "")


def failed_search_answer(code: str, detail: str, language: str) -> str:
    """The chat's reply when a required search failed before any page was read.

    Sent as the answer, never as an error event that leaves the reply empty.
    What failed is said in the person's language: a blocked, timed-out or empty
    search as such, a quota or a limit as a quota or a limit with the
    provider's status (D5), never as an outage.
    """
    from services.orchestrator.chat_pipeline import search_failure_notice
    from services.orchestrator.searxng import OUTCOMES

    language = language if language in SEARCH_FAILED else "en"
    if code in OUTCOMES:
        cause = search_failure_notice(code, language)
    else:
        cause = reason(code, language)
        status = _PROVIDER_STATUS.search(detail)
        if cause and status:
            cause = cause.rstrip(".") + f" (HTTP {status[1]})."
    return SEARCH_FAILED[language] + ("\n\n" + cause if cause else "")


def unavailable(question: str, language: str) -> str:
    labels = {
        "fr": "Je n’ai pas pu récupérer de liens vérifiés. Termes à rechercher :",
        "de": "Ich konnte keine verifizierten Links abrufen. Suchbegriffe:",
        "en": "I could not retrieve verified links. Search terms:",
        "es": "No pude obtener enlaces verificados. Términos de búsqueda:",
        "it": "Non ho potuto recuperare link verificati. Termini di ricerca:",
    }
    terms = re.sub(r"[\n\r`<>#*\[\]]", " ", question)
    terms = re.sub(r"https?://\S+", "", terms)
    return labels.get(language, labels["en"]) + "\n\n" + terms[:500].strip()
