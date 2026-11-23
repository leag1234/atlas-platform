"""Generate the synthetic training set of the local routing classifier.

Every message is written by a model from a description of the kind wanted
(never from a real conversation) and labelled by construction. The output is
services/orchestrator/router_examples.jsonl, one {"text", "kind", "context"}
per line; context names what the assistant delivered just before ("" for a new
conversation). Run against a model gateway (small cost, printed):

    ATLAS_GATEWAY_URL=http://127.0.0.1:8210 python tools/router_synth.py --per 14
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import unicodedata
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

OUTPUT = (
    Path(__file__).resolve().parents[1] / "services/orchestrator/router_examples.jsonl"
)
LANGUAGES = (
    "French",
    "English",
    "German",
    "Spanish",
    "Italian",
    "Portuguese",
    "Dutch",
    "Polish",
    "Swedish",
    "Indonesian",
)
# (kind, context, what the messages ask for)
TOPICS = (
    (
        "answer",
        "",
        "an explanation, advice, an opinion or a how-to question answered in the chat (including questions about files, code, servers or drawings: how to do it, why, what it is)",
    ),
    (
        "answer",
        "",
        "a text written in the chat for the user to copy: an email, a message, a letter, a post, a poem, a speech, a translation, a summary, a list, or a code snippet only to show",
    ),
    ("answer", "", "small talk, thanks, greetings or a reaction that asks for nothing"),
    (
        "research",
        "",
        "current or external facts from the web: news, prices, weather in a named city, opening hours, schedules, recent events, a specific product or company",
    ),
    (
        "research",
        "",
        "something that depends on where the user is right now: places nearby, directions or travel time from here, what is open around them, local weather",
    ),
    (
        "drawing",
        "",
        "a picture to look at: a drawing, illustration, logo, icon, poster, cartoon, diagram or schema",
    ),
    (
        "app",
        "",
        "something interactive to use in the browser: a game, quiz, flashcards, calculator, converter, timer, simulator, music toy, interactive chart or a small web page",
    ),
    (
        "file",
        "",
        "a real file to download: a Word document, Excel spreadsheet, PowerPoint deck, PDF, CSV or Markdown file, a CV as a document",
    ),
    (
        "run",
        "",
        "code to actually execute now, a computation done by running a program, tests to run, or a program to build and run (script, bot, API, scraper)",
    ),
    (
        "remote",
        "",
        "an action on a computer the user owns over SSH: their server, VPS, NAS, Raspberry Pi or laptop (check disk, restart a service, install a package, connect, rename or forget a machine)",
    ),
    (
        "other",
        "",
        "an action in the real world the assistant cannot do here: booking a table, paying, calling someone, sending a text message to a person, switching on a smart-home device",
    ),
    (
        "app",
        "app",
        "a change to the game or app the assistant has just delivered (bigger buttons, dark mode, a new level, fix a bug), often phrased as a question like 'could you...' or 'why does... can you fix it'",
    ),
    (
        "drawing",
        "drawing",
        "a change to the drawing the assistant has just delivered (colours, an added element, a different background)",
    ),
    (
        "file",
        "file",
        "a change to the spreadsheet or document file the assistant has just delivered (add a column, a slide, rename, regenerate)",
    ),
    (
        "run",
        "run",
        "rerunning or changing the program the assistant has just run (other values, more iterations, retry after a crash)",
    ),
    (
        "run",
        "script",
        "asking to execute the script the assistant has just shown as text",
    ),
    (
        "remote",
        "remote",
        "continuing the task on the user's server the assistant has just reported on (go ahead, restart it, same on the other machine)",
    ),
    (
        "answer",
        "app",
        "a question about how the app just delivered works, or thanks for it, asking for no change",
    ),
    # Near misses: questions that mention what other kinds deliver.
    (
        "answer",
        "",
        "a how-to question about Excel, Word, PowerPoint, PDF or CSV files, spreadsheets or documents: the user wants an explanation, not a file",
    ),
    (
        "answer",
        "",
        "a question about code, an error message or a programming concept, or a request to write code and show it without running it",
    ),
    (
        "answer",
        "",
        "a general question about servers, SSH, Linux, Docker or networking (how to, what is, why), not an action on the user's own machine",
    ),
    (
        "answer",
        "",
        "a question about drawing, design, games or apps in general (how to draw, which tool to use, how a game works), not a request to make one",
    ),
    (
        "app",
        "",
        "a game, quiz or small tool asked politely as a question ('could you make me...', 'would it be possible to have...')",
    ),
    (
        "file",
        "",
        "a document, spreadsheet or deck to download asked politely as a question, naming the format or the kind of document",
    ),
    # Very short requests: a verb and an object, as typed on a phone.
    (
        "drawing",
        "",
        "a very short request of 2 to 6 words to draw or illustrate one thing (an animal, an object, a person, a place)",
    ),
    (
        "app",
        "",
        "a very short request of 2 to 6 words for a game, quiz, timer or small tool to use",
    ),
    (
        "file",
        "",
        "a very short request of 2 to 6 words for a file: a spreadsheet, a Word or PDF document, a slide deck, a CSV",
    ),
    (
        "run",
        "",
        "a very short request of 2 to 6 words to run, execute or test code or a script",
    ),
    (
        "research",
        "",
        "a very short question of 2 to 6 words about the weather, news, a price, a score or opening hours",
    ),
    (
        "answer",
        "",
        "a very short message of 1 to 6 words: a greeting, a thanks, a definition to give, a word to translate",
    ),
)

PROMPT = """Write {count} different messages that real users might send to an AI assistant, in {language}, each asking for {topic}.
{context}Vary the subject, the length (2 to 25 words), politeness and register. About a third have typos, missing accents or text-message spelling; some are phrased as questions ("could you ..."), some as orders. Never number them.
Reply with one JSON object only: {{"messages": ["...", "..."]}}"""

CONTEXTS = {
    "": "",
    "app": "The assistant has just delivered an interactive app or game in the side panel.\n",
    "drawing": "The assistant has just delivered a drawing in the side panel.\n",
    "file": "The assistant has just delivered a downloadable file (spreadsheet or document).\n",
    "run": "The assistant has just executed a program and shown its output.\n",
    "script": "The assistant has just shown a script as text, without running it.\n",
    "remote": "The assistant has just reported on the user's server over SSH and asked whether to go on.\n",
}


def fold(text: str) -> str:
    return "".join(
        c
        for c in unicodedata.normalize("NFKD", " ".join(text.casefold().split()))
        if not unicodedata.combining(c)
    )


async def batch(
    kind: str, context: str, topic: str, language: str, count: int
) -> tuple[list[dict[str, str]], Decimal]:
    from packages.ports import atlas_ports
    from services.orchestrator.model import GatewayModel

    payload = {
        "messages": [
            {
                "role": "user",
                "content": PROMPT.format(
                    count=count,
                    language=language,
                    topic=topic,
                    context=CONTEXTS[context],
                ),
            }
        ],
        "tools": [],
        "response_format": {"type": "json_object"},
        "profile": os.environ.get("ROUTER_SYNTH_PROFILE", "atlas-qwen"),
        "max_tokens": 1800,
        "timeout": 90,
        "budget_eur": "0.02",
        "local_enabled": False,
        "reasoning_effort": "none",
        "reasoning_ceiling": "none",
        "reasoning_disabled": True,
    }
    url = os.environ.get("ATLAS_GATEWAY_URL", atlas_ports().url("gateway"))
    try:
        result = await GatewayModel.post(url + "/agent/complete", payload, 95)
        items = json.loads(str(result.get("text") or "{}")).get("messages") or []
        cost = Decimal(str(result.get("cost_eur") or 0))
    except Exception as exc:  # noqa: BLE001 - a failed batch is reported and skipped
        print(
            f"batch failed: {kind}/{context}/{language}: {type(exc).__name__}",
            file=sys.stderr,
        )
        return [], Decimal(0)
    rows = [
        {"text": " ".join(item.split()), "kind": kind, "context": context}
        for item in items
        if isinstance(item, str) and 2 <= len(item.split()) <= 60
    ]
    return rows, cost


async def main(per: int, concurrency: int, start: int) -> None:
    limit = asyncio.Semaphore(concurrency)

    async def one(topic: tuple[str, str, str], language: str):
        async with limit:
            return await batch(*topic, language, per)

    results = await asyncio.gather(
        *(one(topic, language) for topic in TOPICS[start:] for language in LANGUAGES)
    )
    held_out = json.loads(
        (
            Path(__file__).resolve().parents[1] / "tests/fixtures/router_cases.json"
        ).read_text()
    )["cases"]
    excluded = {fold(str(case["text"])) for case in held_out}
    # --from-topic N keeps the examples already written and adds topics N and after.
    rows = (
        [json.loads(line) for line in OUTPUT.read_text().splitlines() if line.strip()]
        if start and OUTPUT.exists()
        else []
    )
    seen = {(fold(row["text"]), row["context"]) for row in rows}
    for found, _ in results:
        for row in found:
            key = (fold(row["text"]), row["context"])
            if key in seen or key[0] in excluded:
                continue
            seen.add(key)
            rows.append(row)
    OUTPUT.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    )
    cost = sum((spent for _, spent in results), Decimal(0))
    print(json.dumps({"examples": len(rows), "cost_eur": str(cost)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--per", type=int, default=14)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--from-topic", type=int, default=0)
    arguments = parser.parse_args()
    asyncio.run(main(arguments.per, arguments.concurrency, arguments.from_topic))
