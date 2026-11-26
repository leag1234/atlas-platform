"""The expectations ledger: every requirement stated across a conversation, checked on each version.

Owner request (2026-11-21): a drum machine lost, two turns later, a feature an
earlier turn had added. These tests run a five-turn drum machine (fictional
content) through the real app turn, with scripted model answers and the real
render check (atlas-app-render image, or the host's Playwright), and the
document and deck counterparts on the content publish_document builds.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from packages.doc_render.schema import Content
from services.orchestrator import app_render, artifact_store, expectations
from services.orchestrator.app_turn import process_app_turn
from services.orchestrator.artifact_tools import current_artifact_message
from services.orchestrator.chat_schema import ChatRequest
from services.orchestrator.expectations import Ledger
from services.orchestrator.interactions import Interaction
from services.orchestrator.loop import Message, Reservation, Turn

OWNER = "drummer@example.test"


def drum_machine(
    *,
    tempo: bool = True,
    save: bool = True,
    colours: tuple[str, str] = ("#1d2433", "#f2b134"),
    lang: str = "fr",
) -> str:
    """A small 16-step drum machine: play, tempo slider, pattern save."""
    words = {
        "fr": (
            "Boîte à rythmes",
            "Lecture",
            "Enregistrer le motif",
            "Motifs enregistrés",
        ),
        "en": ("Drum machine", "Play", "Save pattern", "Saved patterns"),
    }[lang]
    title, play, save_label, saved = words
    tempo_html = (
        '<label for="tempo">Tempo</label>'
        '<input type="range" id="tempo" min="60" max="200" value="120">'
        '<span id="bpm">120 BPM</span>'
        if tempo
        else ""
    )
    save_html = (
        f'<button id="save">{save_label}</button><h2>{saved}</h2><ul id="saved"></ul>'
        if save
        else ""
    )
    tempo_js = (
        "const tempo = document.getElementById('tempo');\n"
        "tempo.addEventListener('input', () => {\n"
        "  document.getElementById('bpm').textContent = tempo.value + ' BPM';\n"
        "  if (timer) { stop(); start(); }\n"
        "});\n"
        "const bpm = () => Number(tempo.value);\n"
        if tempo
        else "const bpm = () => 120;\n"
    )
    save_js = (
        "document.getElementById('save').addEventListener('click', () => {\n"
        "  const patterns = JSON.parse(localStorage.getItem('patterns') || '[]');\n"
        "  patterns.push(grid.map((row) => row.slice()));\n"
        "  try { localStorage.setItem('patterns', JSON.stringify(patterns)); } catch (e) {}\n"
        "  const item = document.createElement('li');\n"
        "  item.textContent = 'Motif ' + patterns.length;\n"
        "  document.getElementById('saved').appendChild(item);\n"
        "});\n"
        if save
        else ""
    )
    background, accent = colours
    return f"""<!DOCTYPE html>
<html lang="{lang}">
<head><meta charset="utf-8"><title>{title}</title>
<style>
body {{ background: {background}; color: #eeeeee; font-family: sans-serif; }}
.step {{ width: 22px; height: 22px; margin: 1px; background: #444b5a; border: 0; }}
.step.on {{ background: {accent}; }}
.step.now {{ outline: 2px solid #ffffff; }}
#play {{ background: {accent}; color: {background}; padding: 8px 16px; }}
</style></head>
<body>
<h1>{title}</h1>
<button id="play">{play}</button>
{tempo_html}
<div id="grid"></div>
{save_html}
<script>
const names = ['Kick', 'Snare', 'Hi-hat', 'Clap'];
const grid = names.map(() => Array(16).fill(false));
const root = document.getElementById('grid');
names.forEach((name, row) => {{
  const line = document.createElement('div');
  line.textContent = name + ' ';
  for (let col = 0; col < 16; col++) {{
    const cell = document.createElement('button');
    cell.className = 'step';
    cell.setAttribute('aria-label', name + ' ' + (col + 1));
    cell.addEventListener('click', () => {{ grid[row][col] = !grid[row][col]; cell.classList.toggle('on'); }});
    line.appendChild(cell);
  }}
  root.appendChild(line);
}});
let timer = null, position = 0, audio = null;
{tempo_js}
function tick() {{
  document.querySelectorAll('.now').forEach((el) => el.classList.remove('now'));
  root.querySelectorAll('div').forEach((line) => line.children[position].classList.add('now'));
  const osc = audio.createOscillator();
  osc.connect(audio.destination);
  osc.start();
  osc.stop(audio.currentTime + 0.05);
  position = (position + 1) % 16;
}}
function start() {{ timer = setInterval(tick, 60000 / bpm() / 4); }}
function stop() {{ clearInterval(timer); timer = null; }}
document.getElementById('play').addEventListener('click', () => {{
  if (!audio) audio = new AudioContext();
  if (timer) stop(); else start();
}});
{save_js}</script>
</body>
</html>"""


def answer(code: str, after: str = "La boîte à rythmes est prête.") -> str:
    return f"```html\n{code}\n```\n{after}"


QUIZ_REQUEST = (
    "fais moi un quiz des couleurs avec un score et un bouton « Rejouer » "
    "à la fin de la partie"
)


def colour_quiz(replay: str = "end", questions: int = 3) -> str:
    """A small colour quiz (fictional content) whose end screen holds the
    replay button: shown when the last question is answered ("end"), shown
    only on a key nobody presses ("never"), or absent ("none")."""
    button = (
        '<button id="replay">Rejouer</button>' if replay in {"end", "never"} else ""
    )
    reveal = {
        "end": "document.getElementById('end').style.display = 'block';",
        "never": (
            "document.getElementById('end').style.display = 'block';\n"
            "    document.addEventListener('keydown', (e) => {\n"
            "      if (e.key === 'F9') document.getElementById('replay').style.display = 'inline-block';\n"
            "    });"
        ),
        "none": "document.getElementById('end').style.display = 'block';",
    }[replay]
    hidden = "#replay { display: none; }" if replay == "never" else ""
    replay_js = (
        "document.getElementById('replay').addEventListener('click', start);"
        if button
        else ""
    )
    return f"""<!DOCTYPE html>
<html lang="fr">
<head><meta charset="utf-8"><title>Quiz des couleurs</title>
<style>
body {{ background: #fdf7ec; color: #2b2118; font-family: sans-serif; }}
#end {{ display: none; }}
.answer {{ display: block; margin: 6px 0; padding: 8px 14px; }}
{hidden}
</style></head>
<body>
<h1>Quiz des couleurs</h1>
<p id="score">Score : 0</p>
<p id="question"></p>
<div id="answers"></div>
<div id="end"><p id="final">Partie terminée</p>{button}</div>
<script>
const all = [
  ['Rouge et jaune donnent ?', ['Orange', 'Vert', 'Gris']],
  ['Bleu et jaune donnent ?', ['Vert', 'Violet', 'Brun']],
  ['Rouge et bleu donnent ?', ['Violet', 'Orange', 'Blanc']],
  ['Noir et blanc donnent ?', ['Gris', 'Rose', 'Bleu']],
];
const items = all.slice(0, {questions});
let index = 0, score = 0;
function show() {{
  const box = document.getElementById('answers');
  box.innerHTML = '';
  if (index >= items.length) {{
    document.getElementById('question').textContent = '';
    {reveal}
    return;
  }}
  const [text, choices] = items[index];
  document.getElementById('question').textContent = text;
  choices.forEach((choice, position) => {{
    const button = document.createElement('button');
    button.className = 'answer';
    button.textContent = choice;
    button.addEventListener('click', () => {{
      if (position === 0) score += 1;
      document.getElementById('score').textContent = 'Score : ' + score;
      index += 1;
      setTimeout(show, 400);
    }});
    box.appendChild(button);
  }});
}}
function start() {{
  index = 0; score = 0;
  document.getElementById('score').textContent = 'Score : 0';
  document.getElementById('end').style.display = 'none';
  show();
}}
{replay_js}
start();
</script>
</body>
</html>"""


# What the turn's model returns for each message (the extraction), as the
# requirements a careful reading gives; the code applies and checks them.
EXTRACTIONS: dict[str, dict[str, Any]] = {
    "crée une boîte à rythmes de 16 pas, 4 instruments, avec un bouton lecture": {
        "new": [
            {
                "statement": "Grille de 16 pas pour 4 instruments",
                "method": "code",
                "role": "button",
                "words": ["kick 16", "snare 16"],
                "action": "none",
                "expect": ["present"],
            },
            {
                "statement": "Bouton lecture qui joue le rythme",
                "method": "code",
                "role": "button",
                "words": ["lecture", "play"],
                "action": "click",
                "expect": ["sound", "changes"],
            },
        ],
        "changed": [],
        "removed": [],
    },
    "ajoute un controle du tempo stp": {
        "new": [
            {
                "statement": "Contrôle du tempo",
                "method": "code",
                "role": "slider",
                "words": ["tempo", "bpm"],
                "action": "set",
                "expect": ["changes"],
            }
        ],
        "changed": [],
        "removed": [],
    },
    "et je veux pouvoir sauvegarder mes motifs": {
        "new": [
            {
                "statement": "Sauvegarde des motifs",
                "method": "code",
                "role": "button",
                "words": ["enregistrer", "sauvegarder", "save"],
                "action": "click",
                "expect": ["stores", "changes"],
            }
        ],
        "changed": [],
        "removed": [],
    },
    "change les couleurs": {
        "new": [
            {
                "statement": "Nouvelles couleurs",
                "method": "render",
                "property": "palette",
            }
        ],
        "changed": [],
        "removed": [],
    },
    "enlève la sauvegarde, ça sert à rien": {
        "new": [],
        "changed": [],
        "removed": [{"id": "R4", "quote": "enlève la sauvegarde"}],
    },
    QUIZ_REQUEST: {
        "new": [
            {
                "statement": "Compteur de score",
                "method": "render",
                "property": "visible",
                "role": "any",
                "words": ["score"],
            },
            {
                "statement": "Bouton « Rejouer » à la fin de la partie",
                "method": "code",
                "role": "button",
                "words": ["rejouer", "replay"],
                "action": "click",
                "expect": ["changes"],
            },
        ],
        "changed": [],
        "removed": [],
    },
}


class ScriptedCalls:
    """The gateway calls of the ledger: extraction by message, judge verdicts."""

    def __init__(self, judge: dict[str, bool] | None = None) -> None:
        self.payloads: list[dict[str, Any]] = []
        self.judge = judge or {}
        # The judge answers without a usable verdict (a failed call).
        self.silent = False

    async def __call__(self, payload: dict[str, Any], seconds: float) -> dict[str, Any]:
        self.payloads.append(payload)
        system = str(payload["messages"][0]["content"])
        data = json.loads(payload["messages"][1]["content"])
        if system.startswith("You keep the list"):
            update = EXTRACTIONS.get(data["latest_message"], {"new": []})
            return {"text": json.dumps(update), "cost_eur": "0.001"}
        if self.silent:
            raise ValueError("no verdict")
        results = [
            {"id": item["id"], "met": self.judge.get(item["id"], True), "why": "seen"}
            for item in data["requirements"]
        ]
        return {"text": json.dumps({"results": results}), "cost_eur": "0.002"}

    def kinds(self) -> list[str]:
        return [
            "extract"
            if str(p["messages"][0]["content"]).startswith("You keep the list")
            else "judge"
            for p in self.payloads
        ]


class FakeModel:
    """The GatewayModel surface an app turn uses, with scripted files."""

    def __init__(self, outcomes: list[str]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[list[Message]] = []
        self.tools: list[Message] = []
        self.sink: Any = None
        self.observing = False
        self.local_enabled = False
        self.reasoning_ceiling: str | None = None
        self.reasoning_room = 2000
        self.cost_ceiling: Decimal | None = None
        self.provider_retries = 0
        self.input_tokens = self.output_tokens = 0
        self.provider_model = "fake"
        self.reasoning = ""
        self.trace_tokens = self.answer_tokens = 0
        self.token_split_estimated = self.reasoning_retried = False
        self.reasoning_used = "none"
        self.generation_ms = 0.0
        self.spent = Decimal(0)
        self.observations: list[object] = []

    def configure_quality(self, profile: str, max_tokens: int, **flags: object) -> None:
        pass

    async def complete(self, messages: list[Message], timeout: float) -> Turn:
        self.calls.append([dict(message) for message in messages])
        text = self.outcomes.pop(0)
        self.spent += Decimal("0.01")
        return Turn(text, (), Reservation(100, Decimal("0.01")))


def _render_mode() -> str:
    if shutil.which("docker") is not None:
        found = subprocess.run(
            ["docker", "image", "inspect", app_render.DEFAULT_IMAGE],
            capture_output=True,
            check=False,
        )
        if found.returncode == 0:
            return "docker"
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            playwright.chromium.launch(headless=True).close()
    except Exception:  # noqa: BLE001 - no usable browser
        return ""
    return "local"


RENDER = _render_mode()


class Conversation(unittest.IsolatedAsyncioTestCase):
    """A conversation whose app turns run process_app_turn for real."""

    render = "off"

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        environment = patch.dict(
            os.environ,
            {
                "ATLAS_DATA_DIR": self.directory.name,
                "ATLAS_ARTIFACTS": "1",
                "ATLAS_APP_RENDER": self.render,
                "ATLAS_EXPECTATIONS": "1",
            },
        )
        environment.start()
        self.addCleanup(environment.stop)
        self.history: list[dict[str, str]] = []
        self.calls = ScriptedCalls()
        complete = patch("services.orchestrator.expectations._complete", self.calls)
        complete.start()
        self.addCleanup(complete.stop)

    async def app_turn(
        self, text: str, files: list[str], intent: str = "modify"
    ) -> tuple[Interaction, FakeModel]:
        request = ChatRequest.model_validate(
            {
                "messages": [*self.history, {"role": "user", "content": text}],
                "lang": "fr",
            }
        )
        request.__dict__["artifact_owner"] = OWNER
        current = current_artifact_message(
            [{"role": m.role, "content": m.text} for m in request.messages], OWNER
        )
        model = FakeModel(files)
        item = Interaction()
        with patch(
            "services.orchestrator.app_turn.GatewayModel.connect",
            AsyncMock(return_value=model),
        ):
            await process_app_turn(
                request,
                item,
                intent="create" if not self.history else intent,
                kind="app",
                current=current,
                wall_clock_limit=600,
            )
        self.history += [
            {"role": "user", "content": text},
            {"role": "assistant", "content": item.reponse},
        ]
        return item, model

    def chat_turn(self, text: str, reply: str) -> None:
        """A turn the chat answers itself (no app turn runs)."""
        self.history += [
            {"role": "user", "content": text},
            {"role": "assistant", "content": reply},
        ]

    def ledger(self, item: Interaction) -> Ledger:
        artifact = item.artifacts[0]
        stored = expectations.load(f"app:{artifact['id']}", int(artifact["version"]))
        assert stored is not None
        return stored[0]

    def stored(self, item: Interaction) -> str:
        artifact = item.artifacts[0]
        return artifact_store.read(
            str(artifact["id"]), OWNER, int(artifact["version"])
        ).content.decode()


@unittest.skipUnless(RENDER, "no render runtime (atlas-app-render image or Playwright)")
class DrumMachineScenario(Conversation):
    """Five turns; every earlier feature is checked in the rendered app."""

    render = RENDER

    async def build_four_turns(self) -> Interaction:
        item, _ = await self.app_turn(
            "crée une boîte à rythmes de 16 pas, 4 instruments, avec un bouton lecture",
            [answer(drum_machine(tempo=False, save=False))],
        )
        self.assertEqual(item.artifacts[0]["version"], 1)
        item, _ = await self.app_turn(
            "ajoute un controle du tempo stp", [answer(drum_machine(save=False))]
        )
        item, _ = await self.app_turn(
            "et je veux pouvoir sauvegarder mes motifs", [answer(drum_machine())]
        )
        self.chat_turn(
            "au fait c'est quoi un charleston ?",
            "Un charleston (hi-hat) est une paire de cymbales actionnée par une pédale.",
        )
        ledger = self.ledger(item)
        self.assertEqual(
            [(r.id, r.status, r.last) for r in ledger.requirements],
            [
                ("R1", "active", "passed"),
                ("R2", "active", "passed"),
                ("R3", "active", "passed"),
                ("R4", "active", "passed"),
            ],
        )
        # The tempo slider was found by its id: pinned for later versions.
        self.assertEqual(ledger.find("R3").bound, "tempo")  # type: ignore[union-attr]
        return item

    async def test_every_feature_is_kept_at_turn_five(self) -> None:
        await self.build_four_turns()
        item, model = await self.app_turn(
            "change les couleurs",
            [answer(drum_machine(colours=("#f4efe6", "#2a6f97")))],
        )
        self.assertEqual(item.artifacts[0]["version"], 4)
        self.assertEqual(len(model.calls), 1)
        ledger = self.ledger(item)
        self.assertEqual(
            [r.last for r in ledger.active()], ["passed"] * 5, ledger.requirements
        )
        self.assertNotIn("Pas encore conforme", item.reponse)
        # The writer read every requirement, the earlier ones included.
        brief = [
            str(m["content"])
            for m in model.calls[0]
            if m["role"] == "system"
            and "Requirements the person stated" in str(m["content"])
        ]
        self.assertEqual(len(brief), 1)
        for statement in (
            "Contrôle du tempo",
            "Sauvegarde des motifs",
            "Nouvelles couleurs",
        ):
            self.assertIn(statement, brief[0])
        # Code checks only: no judge call in five turns, one extraction per app turn.
        self.assertEqual(self.calls.kinds(), ["extract"] * 4)

    async def test_a_dropped_tempo_control_is_caught_and_repaired(self) -> None:
        await self.build_four_turns()
        item, model = await self.app_turn(
            "change les couleurs",
            [
                answer(drum_machine(tempo=False, colours=("#f4efe6", "#2a6f97"))),
                answer(drum_machine(colours=("#f4efe6", "#2a6f97"))),
            ],
        )
        self.assertEqual(len(model.calls), 2)
        repair = str(model.calls[1][-1]["content"])
        self.assertIn('R3 "Contrôle du tempo"', repair)
        self.assertIn("no slider or number field", repair)
        self.assertIn('<input type="range" id="tempo"', self.stored(item))
        self.assertEqual([r.last for r in self.ledger(item).active()], ["passed"] * 5)
        self.assertNotIn("Pas encore conforme", item.reponse)

    async def test_an_unrepaired_drop_is_said_by_name(self) -> None:
        await self.build_four_turns()
        dropped = answer(drum_machine(tempo=False, colours=("#f4efe6", "#2a6f97")))
        item, model = await self.app_turn("change les couleurs", [dropped] * 3)
        # One generation and at most two repairs for the unmet requirement.
        self.assertEqual(len(model.calls), 3)
        self.assertEqual(item.artifacts[0]["version"], 4)
        self.assertIn(
            "Pas encore conforme à votre demande dans cette version : « Contrôle du tempo ».",
            item.reponse,
        )
        ledger = self.ledger(item)
        self.assertEqual(ledger.find("R3").last, "failed")  # type: ignore[union-attr]
        self.assertEqual(ledger.find("R3").status, "active")  # type: ignore[union-attr]

    async def test_a_removed_feature_is_marked_removed_not_failed(self) -> None:
        await self.build_four_turns()
        # The feature comparison (app_features.py) still asks the model,
        # which keeps the file with KEEP: the person asked for the removal.
        item, model = await self.app_turn(
            "enlève la sauvegarde, ça sert à rien",
            [answer(drum_machine(save=False)), "KEEP"],
        )
        self.assertEqual(len(model.calls), 2)
        self.assertNotIn("R4", str(model.calls[1][-1]["content"]))
        ledger = self.ledger(item)
        self.assertEqual(ledger.find("R4").status, "removed")  # type: ignore[union-attr]
        self.assertEqual(ledger.find("R4").last, "passed")  # type: ignore[union-attr]
        self.assertNotIn("Pas encore conforme", item.reponse)
        self.assertNotIn('id="save"', self.stored(item))
        self.assertEqual([r.id for r in ledger.active()], ["R1", "R2", "R3"])


REPLAY = "Bouton « Rejouer » à la fin de la partie"
UNMET = "Pas encore conforme à votre demande dans cette version : « {} »."


@unittest.skipUnless(RENDER, "no render runtime (atlas-app-render image or Playwright)")
class LaterStateScenario(Conversation):
    """A requirement shown only in a later state of the app (staging -151:
    a quiz's replay button, hidden until the last question, was said not to
    work in three versions that had it)."""

    render = RENDER

    async def quiz_turn(self, *files: str) -> tuple[Interaction, FakeModel]:
        return await self.app_turn(
            QUIZ_REQUEST, [answer(code, "Le quiz est prêt.") for code in files]
        )

    async def test_a_replay_button_shown_at_the_end_is_checked_by_playing(
        self,
    ) -> None:
        item, model = await self.quiz_turn(colour_quiz("end"))
        self.assertEqual(len(model.calls), 1)
        ledger = self.ledger(item)
        self.assertEqual([r.last for r in ledger.active()], ["passed", "passed"])
        self.assertEqual(ledger.find("R2").bound, "replay")  # type: ignore[union-attr]
        self.assertNotIn("Pas encore conforme", item.reponse)
        self.assertNotIn("Non vérifié", item.reponse)
        # Reached by playing the quiz in the browser: no judge call.
        self.assertEqual(self.calls.kinds(), ["extract"])

    async def test_a_replay_button_never_reached_is_unchecked_not_failed(
        self,
    ) -> None:
        self.calls.silent = True
        item, model = await self.quiz_turn(colour_quiz("never"))
        # Nothing to repair: an unseen requirement is not a failure.
        self.assertEqual(len(model.calls), 1)
        ledger = self.ledger(item)
        self.assertEqual([r.last for r in ledger.active()], ["passed", "unchecked"])
        self.assertNotIn("Pas encore conforme", item.reponse)
        self.assertIn(f"Non vérifié automatiquement : « {REPLAY} ».", item.reponse)
        # One judge call (tried on both profiles, no verdict) read the file
        # with what the browser saw.
        self.assertEqual(self.calls.kinds(), ["extract", "judge", "judge"])
        asked = json.loads(self.calls.payloads[1]["messages"][1]["content"])
        self.assertIn("hidden when the app opens", asked["requirements"][0]["browser"])

    async def test_the_judge_settles_a_replay_button_never_reached(self) -> None:
        self.calls.judge = {"R2": True}
        item, _ = await self.quiz_turn(colour_quiz("never"))
        self.assertEqual(
            [r.last for r in self.ledger(item).active()], ["passed", "passed"]
        )
        self.assertNotIn("Non vérifié", item.reponse)

    async def test_a_missing_replay_button_is_reported(self) -> None:
        item, model = await self.quiz_turn(*[colour_quiz("none")] * 3)
        # One generation and two repairs: the absence is positive evidence.
        self.assertEqual(len(model.calls), 3)
        self.assertIn("no button", str(model.calls[1][-1]["content"]))
        self.assertEqual(self.ledger(item).find("R2").last, "failed")  # type: ignore[union-attr]
        self.assertIn(UNMET.format(REPLAY), item.reponse)
        self.assertEqual(self.calls.kinds(), ["extract"])

    async def test_a_replay_button_removed_in_a_later_version_is_reported(
        self,
    ) -> None:
        item, _ = await self.quiz_turn(colour_quiz("end"))
        self.assertEqual(self.ledger(item).find("R2").last, "passed")  # type: ignore[union-attr]
        item, _ = await self.app_turn(
            "ajoute une question sur le gris",
            [
                answer(
                    colour_quiz("none", questions=4), "Le quiz a une question de plus."
                )
            ]
            * 4,
        )
        self.assertEqual(item.artifacts[0]["version"], 2)
        self.assertEqual(self.ledger(item).find("R2").last, "failed")  # type: ignore[union-attr]
        self.assertIn(UNMET.format(REPLAY), item.reponse)


class LaterStateRules(unittest.IsolatedAsyncioTestCase):
    """What a browser measurement proves, without a browser."""

    def ledger(self) -> Ledger:
        ledger = Ledger("app")
        ledger.apply(EXTRACTIONS[QUIZ_REQUEST], 1, ["x"])
        return ledger

    def test_only_positive_evidence_fails(self) -> None:
        replay = self.ledger().find("R2")
        assert replay is not None
        cases: list[tuple[dict[str, Any], str, bool | None]] = [
            # Hidden as the app opens, not reached by playing: unchecked.
            ({"found": True, "visible": False, "explored": True}, "", None),
            # Absent from the page, but the file's script creates it.
            (
                {"found": False, "explored": True},
                "b.textContent = 'Rejouer';",
                None,
            ),
            # The words only as script identifiers: absent, failed.
            ({"found": False, "explored": True}, "const replay = 1;", False),
            ({"found": False, "explored": True}, "<p>Fin</p>", False),
            # A label written as an id in camel case still names it.
            ({"found": False}, '<div id="btnReplay"></div>', None),
            # Shown and pressed, nothing changed: failed.
            ({"found": True, "visible": True, "met": []}, "", False),
            (
                {"found": True, "visible": True, "met": ["changes"], "reached": 3},
                "",
                True,
            ),
        ]
        for measured, source, verdict in cases:
            with self.subTest(measured=measured, source=source):
                result = expectations.app_result(replay, measured, source)
                self.assertIs(result.passed, verdict, result.how)

    async def test_an_unseen_requirement_goes_to_the_judge_with_the_measure(
        self,
    ) -> None:
        calls = ScriptedCalls()
        calls.silent = True
        turn = expectations.TurnLedger(
            "app:x", self.ledger(), "atlas-test", "fr", complete=calls
        )
        results = await turn.check_app(
            colour_quiz("never"),
            {
                "R1": {"found": True, "visible": True, "met": ["present"]},
                "R2": {"found": True, "visible": False, "explored": True},
            },
            [],
            30,
        )
        self.assertEqual([r.passed for r in results], [True, None])
        self.assertIn("hidden when the app opens", results[1].how)
        self.assertEqual(expectations.failed(results), [])
        self.assertEqual(
            expectations.status_label("fr", results),
            "1/2 exigences respectées, 1 non vérifiée",
        )
        self.assertEqual(
            expectations.note("fr", turn.ledger, results),
            f"Non vérifié automatiquement : « {REPLAY} ».",
        )

    def test_status_counts_unchecked_in_every_language(self) -> None:
        results = [
            expectations.Result("R1", True),
            expectations.Result("R2", None),
            expectations.Result("R3", None),
        ]
        self.assertEqual(
            expectations.status_label("en", results),
            "1/3 requirements met, 2 not checked",
        )
        self.assertEqual(
            expectations.status_label("fr", results),
            "1/3 exigences respectées, 2 non vérifiées",
        )
        for language in ("de", "es", "it"):
            self.assertIn("2 ", expectations.status_label(language, results))
        self.assertEqual(
            expectations.status_label("en", results[:1]), "1/1 requirements met"
        )


class LedgerWithoutBrowser(Conversation):
    """With the render check off, executable checks go to the judge (bounded)."""

    render = "off"

    async def quiz_turn(self, *files: str) -> tuple[Interaction, FakeModel]:
        return await self.app_turn(
            QUIZ_REQUEST, [answer(code, "Le quiz est prêt.") for code in files]
        )

    async def test_executable_checks_fall_back_to_one_judge_call(self) -> None:
        self.calls.judge = {"R2": False}
        item, model = await self.app_turn(
            "crée une boîte à rythmes de 16 pas, 4 instruments, avec un bouton lecture",
            [answer(drum_machine())] * 3,
        )
        self.assertEqual(len(model.calls), 3)
        self.assertEqual(self.calls.kinds(), ["extract", "judge", "judge", "judge"])
        self.assertIn("« Bouton lecture qui joue le rythme »", item.reponse)
        # The judge's cost is the turn's cost.
        self.assertAlmostEqual(item.cout_eur, 0.03 + 0.001 + 3 * 0.002)

    async def test_failed_check_calls_are_exposed_in_the_turn_record(self) -> None:
        self.calls.silent = True
        item, _ = await self.quiz_turn(colour_quiz("none"))
        step = next(step for step in item.steps if step["phase"] == "expectations")
        self.assertEqual(step["checks_skipped"], 2)

    async def test_the_ledger_off_changes_nothing(self) -> None:
        with patch.dict(os.environ, {"ATLAS_EXPECTATIONS": "0"}):
            item, model = await self.app_turn(
                "crée une boîte à rythmes de 16 pas, 4 instruments, avec un bouton lecture",
                [answer(drum_machine())],
            )
        self.assertEqual(self.calls.payloads, [])
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(item.artifacts[0]["version"], 1)


class LedgerRules(unittest.TestCase):
    """What code keeps, whatever the model returns."""

    def test_unmentioned_requirements_stay(self) -> None:
        ledger = Ledger("app")
        ledger.apply(EXTRACTIONS["ajoute un controle du tempo stp"], 2, ["x"])
        ledger.apply(
            {"new": [], "removed": [{"id": "R1", "quote": "remove the tempo"}]},
            3,
            ["change the colours"],
        )
        self.assertEqual(ledger.find("R1").status, "active")  # type: ignore[union-attr]

    def test_removal_and_change_need_the_persons_words(self) -> None:
        ledger = Ledger("app")
        ledger.apply(
            {"new": [{"statement": "Tempo control", "method": "judge"}]}, 1, ["x"]
        )
        said = ["Mach das Tempo bis 240 BPM einstelbar und lösch den Speichern-Knopf"]
        events = ledger.apply(
            {
                "changed": [
                    {
                        "id": "R1",
                        "statement": "Tempo bis 240 BPM",
                        "method": "judge",
                        "quote": "Mach das Tempo bis 240 BPM einstelbar",
                    }
                ]
            },
            2,
            said,
        )
        self.assertEqual(events, ["changed:R1>R2"])
        self.assertEqual(ledger.find("R1").replaced_by, "R2")  # type: ignore[union-attr]
        # A quote the person did not write keeps the requirement.
        events = ledger.apply(
            {"removed": [{"id": "R2", "quote": "delete the tempo"}]}, 3, said
        )
        self.assertEqual(events, ["kept:R2"])

    def test_duplicates_and_invalid_checks(self) -> None:
        ledger = Ledger("app")
        ledger.apply(
            {
                "new": [
                    {
                        "statement": "Save patterns",
                        "method": "code",
                        "role": "button",
                        "words": [],
                    },
                    {"statement": "save  patterns", "method": "code"},
                    {
                        "statement": "Press space to play",
                        "method": "code",
                        "role": "any",
                        "words": ["play"],
                        "action": "key",
                        "key": "rm -rf",
                    },
                ]
            },
            1,
            ["x"],
        )
        self.assertEqual(
            [r.statement for r in ledger.active()],
            ["Save patterns", "Press space to play"],
        )
        self.assertEqual([r.method for r in ledger.active()], ["judge", "judge"])

    def test_check_fields_nested_by_the_model_are_read(self) -> None:
        # As atlas-mistral returned them on staging (2026-11-21).
        ledger = Ledger("app")
        ledger.apply(
            {
                "new": [
                    {
                        "statement": "Tempo-Regler",
                        "method": {
                            "role": "slider",
                            "words": ["tempo", "bpm"],
                            "action": "set",
                            "expect": ["changes"],
                        },
                    },
                    {"statement": "Wärmere Farben", "check": {"property": "palette"}},
                ]
            },
            1,
            ["x"],
        )
        self.assertEqual(
            [
                (r.method, r.check.get("role") or r.check.get("property"))
                for r in ledger.active()
            ],
            [("code", "slider"), ("render", "palette")],
        )

    def test_the_ledger_survives_storage(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, {"ATLAS_DATA_DIR": directory}),
        ):
            ledger = Ledger("deck")
            ledger.apply(
                {
                    "new": [
                        {
                            "statement": "Une diapositive budget",
                            "method": "code",
                            "part": "slide",
                            "words": ["budget"],
                        }
                    ]
                },
                1,
                ["x"],
            )
            expectations.save("file:x", 1, ledger)
            expectations.save("file:x", 3, Ledger("deck"))
            loaded = expectations.load("file:x", 2)
            assert loaded is not None
            self.assertEqual(loaded[1], 1)
            self.assertEqual(loaded[0].to_json(), ledger.to_json())

    def test_notes_and_status_in_three_languages(self) -> None:
        ledger = Ledger("app")
        ledger.apply(
            {"new": [{"statement": "Tempo control"}, {"statement": "Pattern save"}]},
            1,
            ["x"],
        )
        results = [
            expectations.Result("R1", False, "missing"),
            expectations.Result("R2", True),
        ]
        self.assertEqual(
            expectations.note("en", ledger, results),
            "Not working yet in this version, against what you asked: “Tempo control”.",
        )
        self.assertIn("„Tempo control“", expectations.note("de", ledger, results))
        self.assertEqual(
            expectations.status_label("fr", results), "1/2 exigences respectées"
        )


def _content(title: str, sections: list[dict[str, Any]]) -> Content:
    return Content.model_validate({"title": title, "sections": sections})


class DocumentBase(unittest.IsolatedAsyncioTestCase):
    """A publication's ledger, with scripted model replies."""

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        environment = patch.dict(os.environ, {"ATLAS_DATA_DIR": self.directory.name})
        environment.start()
        self.addCleanup(environment.stop)

    def tools(self, text: str, earlier: list[str], replies: dict[str, Any]) -> Any:
        async def complete(payload: dict[str, Any], seconds: float) -> dict[str, Any]:
            data = json.loads(payload["messages"][1]["content"])
            if "latest_message" in data:
                return {
                    "text": json.dumps(replies.get(data["latest_message"], {"new": []}))
                }
            return {
                "text": json.dumps(
                    {
                        "results": [
                            {"id": r["id"], "met": True} for r in data["requirements"]
                        ]
                    }
                )
            }

        return SimpleNamespace(
            check_model=SimpleNamespace(
                profile="atlas-qwen", reasoning_room=2000, spent=Decimal(0)
            ),
            request_text=text,
            earlier_requests=earlier,
            picture_scope="reader|conversation-1",
            lang="en",
            deliverable_refused=False,
            expectations_complete=complete,
        )

    async def publish(
        self, tools: Any, name: str, content: Content
    ) -> list[expectations.Result]:
        from services.orchestrator.deliverable_check import drawn_text

        results = await expectations.document_check(
            tools, name, "." + name.rsplit(".", 1)[1], content, drawn_text(content), 60
        )
        expectations.document_refusal(tools, results)
        expectations.document_saved(tools, name)
        return results


class DocumentScenarios(DocumentBase):
    """Documents and decks: requirements checked on the built content."""

    async def test_an_added_section_survives_a_later_title_change(self) -> None:
        replies = {
            "Write a short report on the Brookfield community garden": {
                "new": [
                    {
                        "statement": "Report on the Brookfield community garden",
                        "method": "judge",
                    }
                ]
            },
            "add a section about the volunteers pls": {
                "new": [
                    {
                        "statement": "Section about the volunteers",
                        "method": "code",
                        "part": "section",
                        "words": ["volunteer"],
                    }
                ]
            },
            "change the title to Brookfield Garden 2026": {
                "changed": [],
                "new": [
                    {
                        "statement": "Title: Brookfield Garden 2026",
                        "method": "code",
                        "part": "title",
                        "words": ["Brookfield Garden 2026"],
                    }
                ],
            },
        }
        first = _content(
            "Brookfield community garden",
            [{"heading": "Harvest", "paragraphs": ["Tomatoes and beans."]}],
        )
        await self.publish(
            self.tools(
                "Write a short report on the Brookfield community garden", [], replies
            ),
            "report.docx",
            first,
        )
        second = _content(
            "Brookfield community garden",
            [
                {"heading": "Harvest", "paragraphs": ["Tomatoes and beans."]},
                {"heading": "Our volunteers", "paragraphs": ["Twelve neighbours."]},
            ],
        )
        await self.publish(
            self.tools(
                "add a section about the volunteers pls",
                ["Write a short report on the Brookfield community garden"],
                replies,
            ),
            "report.docx",
            second,
        )
        # The title change rewrote the file and dropped the section: refused.
        tools = self.tools(
            "change the title to Brookfield Garden 2026",
            [
                "Write a short report on the Brookfield community garden",
                "add a section about the volunteers pls",
            ],
            replies,
        )
        dropped = _content(
            "Brookfield Garden 2026",
            [{"heading": "Harvest", "paragraphs": ["Tomatoes and beans."]}],
        )
        with self.assertRaises(ValueError) as refused:
            await self.publish(tools, "report.docx", dropped)
        self.assertIn('"Section about the volunteers"', str(refused.exception))
        self.assertIn('no section heading contains "volunteer"', str(refused.exception))
        fixed = _content("Brookfield Garden 2026", second.model_dump()["sections"])
        results = await self.publish(tools, "report.docx", fixed)
        self.assertEqual([r.passed for r in results], [True, True, True])
        stored = expectations.load(
            expectations.document_key("reader|conversation-1", "report.docx")
        )
        assert stored is not None
        self.assertEqual(stored[1], 3)

    async def test_a_deck_keeps_its_slides_and_a_failure_is_bounded(self) -> None:
        replies = {
            "Make a 4-slide deck for the Harbourside choir concert, with a slide on ticket prices": {
                "new": [
                    {
                        "statement": "Four slides",
                        "method": "code",
                        "part": "slide",
                        "count": 4,
                    },
                    {
                        "statement": "Slide on ticket prices",
                        "method": "code",
                        "part": "slide",
                        "words": ["ticket", "price"],
                    },
                ]
            },
        }
        tools = self.tools(
            "Make a 4-slide deck for the Harbourside choir concert, with a slide on ticket prices",
            [],
            replies,
        )
        three = _content(
            "Harbourside choir",
            [
                {"heading": h, "bullets": ["…"]}
                for h in ("Programme", "Singers", "Venue")
            ],
        )
        with self.assertRaises(ValueError):
            await self.publish(tools, "concert.pptx", three)
        # A document is refused once a turn (the refusal is shared with
        # deliverable_check): the repair is published and its gaps name both
        # requirements.
        results = await self.publish(tools, "concert.pptx", three)
        gaps = expectations.document_gaps(tools, results)
        self.assertEqual(len(gaps), 2)
        self.assertIn("the file has 3 slides, at least 4 were asked", gaps[0])
        self.assertIn("ticket", gaps[1])


def _renderer() -> bool:
    from services.retrieval import deliverable_render

    return (
        deliverable_render.tools() is not None
        and deliverable_render.isolation() is not None
    )


PARAGRAPH = (
    "The Port-Claret harbour market gathers fishers, bakers and growers every "
    "Saturday morning. "
) * 12


class SizeRequirements(DocumentBase):
    """Sizes and counts the person asked, measured on the built file."""

    def build(self, content: Content, suffix: str) -> bytes:
        from services.orchestrator.presentation_delivery import (
            build_project_document,
        )

        return build_project_document(
            content, suffix, [], max_bytes=16 * 1024 * 1024, timeout=30
        ).content

    async def check(
        self, tools: Any, name: str, content: Content, raw: bytes = b""
    ) -> list[expectations.Result]:
        from services.orchestrator.deliverable_check import drawn_text

        suffix = "." + name.rsplit(".", 1)[1]
        return await expectations.document_check(
            tools, name, suffix, content, drawn_text(content), 60, raw=raw
        )

    async def test_cinq_diapositives_counts_the_slides_of_the_deck(self) -> None:
        asked = "prépare un diaporama de cinq diapositives sur le marché de Port-Claret"
        replies = {
            asked: {
                "new": [
                    {
                        "statement": "Cinq diapositives",
                        "method": "code",
                        "part": "size",
                        "unit": "slides",
                        "count": 5,
                        "relation": "exact",
                    }
                ]
            }
        }
        tools = self.tools(asked, [], replies)
        seven = _content(
            "Marché de Port-Claret",
            [
                {"heading": f"Partie {n}", "bullets": ["Poissons, pain, légumes"]}
                for n in range(6)
            ]
            + [{"heading": "Sources", "bullets": ["Archives de la ville"]}],
        )
        results = await self.check(
            tools, "marche.pptx", seven, self.build(seven, ".pptx")
        )
        self.assertEqual(results[0].passed, False)
        self.assertIn("the file has 7 slides, exactly 5 were asked", results[0].how)
        self.assertIn("Sources", results[0].how)
        with self.assertRaises(ValueError) as refused:
            expectations.document_refusal(tools, results)
        self.assertIn("Cinq diapositives", str(refused.exception))
        five = _content("Marché de Port-Claret", seven.model_dump()["sections"][:5])
        results = await self.check(
            tools, "marche.pptx", five, self.build(five, ".pptx")
        )
        self.assertEqual([result.passed for result in results], [True])

    @unittest.skipUnless(_renderer(), "no LibreOffice renderer with network isolation")
    async def test_about_one_page_is_measured_on_the_rendered_pdf(self) -> None:
        asked = "Write me a short report on the Port-Claret market, about one page"
        replies = {
            asked: {
                "new": [
                    {
                        "statement": "Report on the Port-Claret market",
                        "method": "judge",
                    },
                    {
                        "statement": "About one page",
                        "method": "code",
                        "part": "size",
                        "unit": "pages",
                        "count": 1,
                        "relation": "about",
                    },
                ]
            }
        }
        tools = self.tools(asked, [], replies)
        # Six pages, an unrequested Sources section included.
        long = _content(
            "Port-Claret market",
            [{"heading": f"Part {n}", "paragraphs": [PARAGRAPH]} for n in range(5)]
            + [{"heading": "Sources", "bullets": ["Town archive", "Market board"]}],
        )
        results = await self.check(
            tools, "market.docx", long, self.build(long, ".docx")
        )
        size = results[1]
        self.assertEqual(size.passed, False)
        self.assertRegex(size.how, r"the file has \d+ pages, about 1 \(from 0 to 1\)")
        self.assertIn("an added sources, appendix or summary section counts", size.how)
        self.assertIn("Sources", size.how)
        short = _content(
            "Port-Claret market",
            [{"heading": "Overview", "paragraphs": [PARAGRAPH[:900]]}],
        )
        results = await self.check(
            tools, "market.docx", short, self.build(short, ".docx")
        )
        self.assertEqual([result.passed for result in results], [True, True])

    async def test_maximal_300_woerter_counts_the_words(self) -> None:
        asked = "Schreib mir eine Zusammenfassung über den Markt von Port-Claret, maximal 300 Wörter"
        replies = {
            asked: {
                "new": [
                    {
                        "statement": "Höchstens 300 Wörter",
                        "method": "code",
                        "part": "size",
                        "unit": "words",
                        "count": 300,
                        "relation": "max",
                    }
                ]
            }
        }
        tools = self.tools(asked, [], replies)
        words = "Der Markt von Port-Claret ist jeden Samstag geöffnet. "
        long = _content("Markt", [{"heading": "Überblick", "paragraphs": [words * 50]}])
        results = await self.check(tools, "markt.docx", long)
        self.assertEqual(results[0].passed, False)
        self.assertIn("at most 300 were asked", results[0].how)
        short = _content(
            "Markt", [{"heading": "Überblick", "paragraphs": [words * 30]}]
        )
        results = await self.check(tools, "markt.docx", short)
        self.assertEqual([result.passed for result in results], [True])

    def test_tolerance_and_invalid_sizes(self) -> None:
        self.assertEqual(
            [expectations.tolerance(n) for n in (1, 5, 10, 300)], [1, 1, 2, 60]
        )
        ledger = Ledger("document")
        ledger.apply(
            {
                "new": [
                    {
                        "statement": "Ten pages",
                        "method": "code",
                        "part": "size",
                        "unit": "pages",
                        "count": 10,
                        "relation": "exact",
                    },
                    {
                        "statement": "Many words",
                        "method": "code",
                        "part": "size",
                        "unit": "words",
                        "count": "lots",
                    },
                    {
                        "statement": "Some chapters",
                        "method": "code",
                        "part": "size",
                        "unit": "chapters",
                        "count": 3,
                    },
                ]
            },
            1,
            ["x"],
        )
        self.assertEqual(
            [r.method for r in ledger.active()], ["code", "judge", "judge"]
        )
        # Pages are never counted exactly: "exact" reads as about.
        ten = ledger.active()[0]
        self.assertTrue(expectations.size_result(ten, None, "", {"pages": 12}).passed)
        self.assertFalse(expectations.size_result(ten, None, "", {"pages": 13}).passed)
        self.assertIsNone(
            expectations.size_result(ten, None, "", {"pages": None}).passed
        )


if __name__ == "__main__":
    unittest.main()
