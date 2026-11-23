"""Voice button in the composer, as in other assistants' dictation: real pinned Open WebUI shell.

The dictation relay is replaced by a scripted socket in the page (utterances, pauses, pieces,
final pass), so the check needs no speech engine, no provider key and no shared instance:

- an Open WebUI container (ATLAS_WEBUI_IMAGE, else the pinned image) with the worktree's
  chat-ui.js and chat-ui.css, trusted-header sign-in, its models served by a local stub that
  records each sent message;
- headless Chromium with a fake microphone; /api/atlas/dictation/config and the capture
  worklet are served by the test.

Checks (DICTATION_UI_MODE=after, the default): a click starts and the state is on the button
(aria-pressed, red, tooltip with time, place of processing and keys, live region); each paused
piece is written into the composer while listening, once, and nothing else changes in the
composer; a second click stops and leaves the text in the field without sending; Enter while
dictating stops, waits for the final text and sends it; Enter with a late final pass sends the
live text after a short wait; Escape cancels and keeps only what was already written; a
refused microphone gives a sentence in a bubble above the composer, never over the text
field; no dictation panel, no horizontal scroll, no page error; light, dark and phone layouts.
DICTATION_UI_MODE=before only captures the older panel for comparison.

Usage: python tests/dictation_composer_browser.py OUT_DIR [SOURCE_DIR]
(SOURCE_DIR holds services/orchestrator/chat-ui.js and chat-ui.css, default: this worktree.)
"""

from collections.abc import Iterator
from contextlib import contextmanager
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from typing import Any
from unittest.mock import patch

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from playwright.sync_api import Browser, Page, Route, ViewportSize, sync_playwright
import uvicorn

from packages.profiles import PROFILES
from services.orchestrator.serving import WEBUI_IMAGE
from services.orchestrator.webui import configure

OUT = Path(sys.argv[1])
SOURCE = Path(sys.argv[2] if len(sys.argv) > 2 else ".").resolve()
MODE = os.environ.get("DICTATION_UI_MODE", "after")
PORT, PROVIDER_PORT = 17960, 22980
NAME = f"atlas-dictation-composer-{PORT}"
URL = f"http://127.0.0.1:{PORT}"
DESKTOP: ViewportSize = {"width": 1440, "height": 900}
PHONE: ViewportSize = {"width": 390, "height": 844}
OUT.mkdir(parents=True, exist_ok=True)

# Scripted speech per interface language: partials, then the utterance's final text.
SPEECH = {
    "en": [
        (
            ["Book a table", "Book a table for four"],
            "Book a table for four people on Friday.",
        ),
        (
            ["At the Italian", "At the Italian place near"],
            "At the Italian place near the station.",
        ),
        (["And ask them", "And ask them about parking"], "And ask them about parking."),
    ],
    "fr": [
        (
            ["Réserve une table", "Réserve une table pour quatre"],
            "Réserve une table pour quatre personnes vendredi.",
        ),
        (
            ["Au bureau de", "Au bureau de Lyon près de"],
            "Au bureau de Lyon près de la gare.",
        ),
        (
            ["Et demande-leur", "Et demande-leur pour le parking"],
            "Et demande-leur pour le parking.",
        ),
    ],
}

sent: list[str] = []
app = FastAPI()


@app.get("/v1/models")
async def models() -> dict[str, Any]:
    return {
        "object": "list",
        "data": [
            {"id": PROFILES[0], "object": "model", "name": "Atlas", "owned_by": "atlas"}
        ],
    }


@app.post("/v1/chat/completions")
async def completion(request: Request) -> Any:
    body = await request.json()
    if not body.get("stream"):
        return {
            "id": "synthetic-task",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": '{"title":"Dictation"}',
                    },
                    "finish_reason": "stop",
                }
            ],
        }
    user = [m for m in body.get("messages", []) if m.get("role") == "user"]
    content = user[-1]["content"] if user else ""
    if isinstance(content, list):
        content = " ".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    sent.append(str(content))

    def chunks() -> Iterator[str]:
        for delta, finish in (
            ({"role": "assistant", "content": "Noted."}, None),
            ({}, "stop"),
        ):
            yield (
                "data: "
                + json.dumps(
                    {
                        "id": "synthetic",
                        "object": "chat.completion.chunk",
                        "choices": [
                            {"index": 0, "delta": delta, "finish_reason": finish}
                        ],
                    }
                )
                + "\n\n"
            )
        yield "data: [DONE]\n\n"

    return StreamingResponse(chunks(), media_type="text/event-stream")


def docker(*args: str) -> str:
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, check=True
    ).stdout.strip()


@contextmanager
def shell() -> Iterator[None]:
    import urllib.request

    for port in (PORT, PROVIDER_PORT):
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(("127.0.0.1", port))
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=PROVIDER_PORT, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    options = [
        "run",
        "-d",
        "--rm",
        "--name",
        NAME,
        "--network",
        "host",
        "--label",
        "atlas.purpose=dictation-ui-test",
        "--entrypoint",
        "bash",
    ]
    environment = {
        "PORT": str(PORT),
        "WEBUI_NAME": "Atlas",
        "ATLAS_STABLE": "1",
        "WEBUI_AUTH_TRUSTED_EMAIL_HEADER": "X-Atlas-User",
        "WEBUI_AUTH_TRUSTED_ROLE_HEADER": "X-Atlas-User-Role",
        "DEFAULT_USER_ROLE": "user",
        "ENABLE_SIGNUP": "False",
        "ENABLE_OLLAMA_API": "False",
        "ENABLE_OPENAI_API": "True",
        "OPENAI_API_BASE_URL": f"http://127.0.0.1:{PROVIDER_PORT}/v1",
        "OPENAI_API_KEY": "synthetic-local-only",
        "ENABLE_VERSION_UPDATE_CHECK": "False",
        "OFFLINE_MODE": "True",
        "HF_HUB_OFFLINE": "1",
        "DEFAULT_LOCALE": "",
        "ENABLE_MEMORIES": "False",
        "ENABLE_AUTOMATIONS": "False",
        "ENABLE_NOTES": "False",
        "ENABLE_EASTER_EGGS": "False",
        "ENABLE_CALENDAR": "False",
        "ENABLE_PERSISTENT_CONFIG": "False",
        "ENABLE_NOTIFICATIONS": "False",
        "ENABLE_TITLE_GENERATION": "False",
        "ENABLE_FOLLOW_UP_GENERATION": "False",
        "ENABLE_TAGS_GENERATION": "False",
        "UI_VISIBILITY": '{"chat.artifacts":false,"chat.code_preview":false}',
        "PYTHONPATH": "/app/backend",
    }
    for key, value in environment.items():
        options.extend(["-e", key + "=" + value])
    static = "/app/backend/open_webui/static/atlas/"
    mounts = [
        (Path("packages").resolve(), "/app/backend/packages"),
        (
            Path("prompts/activity-labels.json").resolve(),
            "/app/backend/prompts/activity-labels.json",
        ),
        (Path("assets/brand").resolve(), static + "brand"),
        (Path("services/orchestrator/static/fonts").resolve(), static + "fonts"),
        (
            Path("services/orchestrator/static/atlas-version.json").resolve(),
            static + "atlas-version.json",
        ),
    ]
    for name in ("chat-ui.js", "chat-ui.css"):
        mounts.append((SOURCE / "services/orchestrator" / name, static + name))
    for name in (
        "guest-chat.js",
        "brand-ui.js",
        "stable-auth.js",
        "settings-ui.js",
        "settings-locales.json",
    ):
        mounts.append((Path("services/orchestrator", name).resolve(), static + name))
    for source, target in mounts:
        options.extend(["-v", f"{source}:{target}:ro"])
    options.extend([WEBUI_IMAGE, "/app/backend/start.sh"])
    try:
        docker(*options)
        tags = (
            '<link rel="stylesheet" href="/static/atlas/chat-ui.css">'
            + "".join(
                f'<script src="/static/atlas/{name}"></script>'
                for name in (
                    "stable-auth.js",
                    "brand-ui.js",
                    "chat-ui.js",
                    "guest-chat.js",
                )
            )
            + '<script type="module" src="/static/atlas/settings-ui.js"></script>'
        )
        docker(
            "exec",
            NAME,
            "python",
            "-c",
            'from pathlib import Path; p=Path("/app/build/index.html"); p.write_text(p.read_text().replace("</head>", '
            + repr(tags)
            + '+"</head>"))',
        )
        for _ in range(180):
            try:
                with urllib.request.urlopen(URL + "/health", timeout=1) as reply:
                    if reply.status == 200:
                        break
            except OSError:
                time.sleep(1)
        else:
            raise RuntimeError("disposable_ui_not_ready")
        with patch.dict(os.environ, {"ATLAS_STABLE": "1"}):
            configure(
                URL,
                {"X-Atlas-User": "bootstrap@localhost", "X-Atlas-User-Role": "admin"},
            )
        print("SHELL READY", WEBUI_IMAGE.split("@")[0], flush=True)
        yield
    finally:
        subprocess.run(["docker", "rm", "-f", NAME], capture_output=True)
        server.should_exit = True
        thread.join(timeout=10)


# The dictation relay, scripted in the page: the same messages as services/orchestrator/dictation.py.
FAKE_SOCKET = """
(() => {
  const Native = window.WebSocket;
  class Fake {
    constructor(url) {
      this.url = url; this.readyState = 0; this.binaryType = 'blob'; this.finals = []; this.base = 0; this.open = -1; this.timers = []; this.frames = 0;
      window.__dict = window.__dict || { sent: [], events: [], sockets: 0, closed: 0 };
      window.__dict.sockets += 1; window.__dict.socket = this;
      setTimeout(() => { this.readyState = 1; if (this.onopen) this.onopen({}); }, 60);
    }
    emit(data) { if (this.readyState !== 1) return; window.__dict.events.push(data.type + (data.type === 'partial' ? ':' + data.text : '')); if (this.onmessage) this.onmessage({ data: JSON.stringify(data) }); }
    later(ms, fn) { this.timers.push(setTimeout(fn, ms)); }
    send(data) {
      if (typeof data !== 'string') { this.frames += 1; return; }
      const message = JSON.parse(data), plan = window.__dictPlan;
      window.__dict.sent.push(message.type);
      if (message.type === 'start') {
        this.later(150, () => this.emit({ type: 'ready', engine: 'cloud', privacy: 'standard', audio_out: true, text_out: true, cleanup: true, max_seconds: 600 }));
        let at = 300;
        plan.speech.forEach(([partials, final], index) => {
          for (const text of partials) { at += 350; this.later(at, () => { this.open = index; window.__dict.open = index; this.emit({ type: 'partial', text }); }); }
          at += 400; this.later(at, () => { this.open = -1; window.__dict.open = -1; this.finals.push(final); this.emit({ type: 'final', index: this.finals.length - 1, text: final }); });
          at += 250; const end = at; this.later(at, () => this.emit({ type: 'turn', event: 'end', time_s: end / 1000 }));
          at += plan.gap;
        });
      } else if (message.type === 'commit') {
        const to = this.finals.length, text = this.finals.slice(this.base, to).join(' '); this.base = to;
        this.later(plan.pieceDelay, () => this.emit({ type: 'transcript', last: false, to, piece: to, text }));
      } else if (message.type === 'stop') {
        this.timers.forEach(clearTimeout); this.timers = [];
        const rest = this.finals.slice(this.base).concat(this.open >= 0 ? [plan.speech[this.open][1]] : []).join(' ');
        this.later(plan.stopDelay, () => { this.emit({ type: 'transcript', last: true, text: rest }); this.later(100, () => this.emit({ type: 'done' })); });
      }
    }
    close() { this.readyState = 3; this.timers.forEach(clearTimeout); window.__dict.closed += 1; }
  }
  window.WebSocket = function (url, protocols) { return String(url).includes('/api/atlas/dictation/stream') ? new Fake(url) : new Native(url, protocols); };
  Object.assign(window.WebSocket, { CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3 });
  window.WebSocket.prototype = Native.prototype;
  // A synthetic microphone (a quiet tone): the capture path runs as with a real device.
  navigator.mediaDevices.getUserMedia = async () => {
    const context = new AudioContext(), tone = context.createOscillator(), gain = context.createGain(), out = context.createMediaStreamDestination();
    gain.gain.value = 0.05; tone.connect(gain).connect(out); tone.start();
    return out.stream;
  };
  // The capture worklet from a blob: worklet module loads do not reach the test's routes.
  const addModule = AudioWorklet.prototype.addModule;
  AudioWorklet.prototype.addModule = function (url, options) {
    if (String(url).includes('dictation-worklet.js')) url = URL.createObjectURL(new Blob([window.__dictWorklet], { type: 'text/javascript' }));
    return addModule.call(this, url, options);
  };
})();
"""

# Test-only observer: composer mutations that no Atlas write explains (an insert event less
# than 500 ms before), while the user speaks.
WATCH = """() => {
  const target = document.querySelector('#message-input-container');
  window.__watch = { stray: 0, inserts: 0, last: -1e9, seen: [] };
  addEventListener('atlas:dictation-insert', () => { window.__watch.inserts += 1; window.__watch.last = performance.now(); });
  new MutationObserver(records => {
    const w = window.__watch;
    records = records.filter(r => r.attributeName !== 'data-atlas-voice'); // guest-chat.js re-marks the native microphone
    if (records.length && performance.now() - w.last > 500) { w.stray += records.length; if (w.seen.length < 20) w.seen.push(records.map(r => r.type + ':' + (r.target.nodeName || '') + ':' + (r.attributeName || '')).join(',')); }
  }).observe(target, { subtree: true, childList: true, characterData: true, attributes: true });
}"""

results: dict[str, Any] = {"checks": []}
failures: list[str] = []


def check(name: str, condition: bool, detail: object = "") -> None:
    results["checks"].append([name, bool(condition), detail])
    print(
        ("PASS " if condition else "FAIL ")
        + name
        + ("" if condition else f" :: {detail}"),
        flush=True,
    )
    if not condition:
        failures.append(f"{name}: {detail}")


def worklet(route: Route) -> None:
    route.fulfill(
        status=200,
        content_type="text/javascript",
        body=Path("services/orchestrator/dictation-worklet.js").read_text(),
    )


def open_chat(
    browser: Browser,
    lang: str,
    theme: str,
    viewport: ViewportSize,
    plan: dict[str, Any],
    user: str,
) -> tuple[Page, list[str]]:
    phone = viewport["width"] < 768
    context = browser.new_context(
        viewport=viewport,
        locale={"en": "en-US", "fr": "fr-FR"}[lang],
        permissions=["microphone"],
        is_mobile=phone,
        has_touch=phone,
        device_scale_factor=2 if phone else 1,
        extra_http_headers={
            "X-Atlas-User": user + "@localhost",
            "X-Atlas-User-Role": "user",
        },
    )
    context.add_init_script(
        f"localStorage.setItem('locale', '{lang}'); localStorage.setItem('theme', '{theme}'); window.__dictPlan = {json.dumps(plan)}; window.__dictWorklet = {json.dumps(Path('services/orchestrator/dictation-worklet.js').read_text())};"
        + FAKE_SOCKET
    )
    context.route(
        "**/api/atlas/dictation/config",
        lambda route: route.fulfill(
            status=200, content_type="application/json", body='{"enabled": true}'
        ),
    )
    context.route("**/api/atlas/dictation/dictation-worklet.js", worklet)
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(error.message))
    page.goto(URL + "/", wait_until="domcontentloaded")
    page.wait_for_selector("#chat-input", timeout=90000)
    page.wait_for_timeout(1500)
    for _ in range(3):
        if not page.locator(".modal").count():
            break
        page.locator(".modal button").last.click()
        page.wait_for_timeout(800)
    page.wait_for_selector(".atlas-dictation-button:not([hidden])", timeout=30000)
    page.wait_for_timeout(600)
    return page, errors


def shot(page: Page, name: str, viewport: ViewportSize) -> None:
    page.mouse.move(viewport["width"] // 2, 40)
    if viewport["width"] < 768:
        page.screenshot(path=str(OUT / name), caret="initial")
        return
    box = page.locator("#message-input-container").bounding_box()
    assert box is not None
    top = max(0, box["y"] - 220)
    page.screenshot(
        path=str(OUT / name),
        caret="initial",
        clip={
            "x": max(0, box["x"] - 40),
            "y": top,
            "width": min(box["width"] + 80, viewport["width"]),
            "height": min(box["y"] + box["height"] + 40, viewport["height"]) - top,
        },
    )


def composer_text(page: Page) -> str:
    return str(page.evaluate("() => document.querySelector('#chat-input').innerText"))


def state(page: Page) -> dict[str, Any]:
    return dict(
        page.evaluate("""() => {
      const b = document.querySelector('.atlas-dictation-button'), e = document.querySelector('#chat-input').getBoundingClientRect(), r = b.getBoundingClientRect();
      const note = document.querySelector('.atlas-dictation-note'), status = document.querySelector('.atlas-dictation-status');
      const overlap = !(r.right <= e.left || r.left >= e.right || r.bottom <= e.top || r.top >= e.bottom);
      return { state: b.dataset.state, pressed: b.getAttribute('aria-pressed'), label: b.getAttribute('aria-label'), title: b.title, background: getComputedStyle(b).backgroundColor,
               status: status ? status.textContent : null, statusLive: status ? status.getAttribute('aria-live') : null, panel: document.querySelectorAll('.atlas-dictation-panel').length,
               noteShown: Boolean(note && !note.hidden), overlap, scroll: document.documentElement.scrollWidth <= innerWidth, dark: document.documentElement.classList.contains('dark') };
    }""")
    )


def wait_text(page: Page, word: str, timeout: int = 20000) -> None:
    page.wait_for_function(
        "word => document.querySelector('#chat-input').innerText.includes(word)",
        arg=word,
        timeout=timeout,
    )


def plan(lang: str, **change: Any) -> dict[str, Any]:
    return {
        "speech": SPEECH[lang],
        "gap": 1500,
        "pieceDelay": 300,
        "stopDelay": 700,
        **change,
    }


def keyword(lang: str, index: int) -> str:
    return SPEECH[lang][index][1].rstrip(".").split()[-1]


def flow_click(
    browser: Browser, lang: str, theme: str, viewport: ViewportSize, layout: str
) -> None:
    name = f"after-{lang}-{theme}-{layout}-click"
    page, errors = open_chat(browser, lang, theme, viewport, plan(lang), name)
    shot(page, name + "-0-idle.png", viewport)
    idle = state(page)
    check(
        f"{name} idle: microphone label, not pressed",
        idle["pressed"] == "false" and idle["state"] == "idle",
        idle,
    )
    check(f"{name} button never over the text field", not idle["overlap"], idle)
    prefix = "Note: " if lang == "en" else "Note : "
    page.click("#chat-input")
    page.keyboard.type(prefix)
    page.mouse.move(viewport["width"] // 2, 40)
    page.wait_for_timeout(800)
    page.evaluate(WATCH)
    before = len(sent)
    page.click(".atlas-dictation-button")
    page.wait_for_function(
        "() => document.querySelector('.atlas-dictation-button').dataset.state === 'live'",
        timeout=10000,
    )
    live = state(page)
    check(
        f"{name} listening: aria-pressed and stop label",
        live["pressed"] == "true"
        and live["label"]
        == ("Stop dictation" if lang == "en" else "Arrêter la dictée"),
        live,
    )
    check(
        f"{name} listening: red button",
        live["background"] in ("rgb(217, 45, 32)", "rgb(255, 69, 58)"),
        live["background"],
    )
    partner = "partner services" if lang == "en" else "services partenaires"
    keys = "Enter" if lang == "en" else "Entrée"
    check(
        f"{name} tooltip: time, partner note and keys",
        ("0:0" in live["title"]) and partner in live["title"] and keys in live["title"],
        live["title"],
    )
    check(
        f"{name} live region announces listening",
        live["statusLive"] == "polite"
        and live["status"] == ("Listening" if lang == "en" else "À l’écoute"),
        live,
    )
    check(
        f"{name} no dictation panel", live["panel"] == 0 and not live["noteShown"], live
    )
    wait_text(page, keyword(lang, 0))
    page.wait_for_timeout(500)
    shot(page, name + "-1-first-piece.png", viewport)
    going = state(page)
    check(
        f"{name} first piece in the field while listening",
        going["state"] == "live",
        going,
    )
    wait_text(page, keyword(lang, 1))
    page.wait_for_function("() => (window.__dict || {}).open === 2", timeout=10000)
    page.wait_for_timeout(300)
    shot(page, name + "-2-second-piece.png", viewport)
    page.click(".atlas-dictation-button")
    page.wait_for_function(
        "() => document.querySelector('.atlas-dictation-button').dataset.state === 'idle'",
        timeout=15000,
    )
    page.wait_for_timeout(600)
    shot(page, name + "-3-stopped.png", viewport)
    text = composer_text(page)
    done = state(page)
    watch = page.evaluate("() => window.__watch")
    check(
        f"{name} stopped: text stays in the field",
        text.startswith(prefix.strip())
        and all(keyword(lang, i) in text for i in range(3)),
        text,
    )
    check(
        f"{name} each piece once",
        all(text.count(keyword(lang, i)) == 1 for i in range(3)),
        text,
    )
    check(
        f"{name} three writes, nothing else changed in the composer",
        watch["inserts"] == 3 and watch["stray"] == 0,
        watch,
    )
    check(f"{name} click does not send", len(sent) == before, sent[before:])
    check(
        f"{name} stopped: not pressed, mic label back",
        done["pressed"] == "false"
        and done["label"] == ("Dictate" if lang == "en" else "Dicter"),
        done,
    )
    check(
        f"{name} protocol: start, commits, stop",
        page.evaluate("() => window.__dict.sent")
        == ["start", "commit", "commit", "stop"],
        page.evaluate("() => window.__dict.sent"),
    )
    check(f"{name} no horizontal scroll", done["scroll"], done)
    check(f"{name} theme applied", done["dark"] == (theme == "dark"), done)
    check(f"{name} no page error", not errors, errors)
    page.context.close()


def flow_enter(
    browser: Browser,
    lang: str,
    theme: str,
    viewport: ViewportSize,
    layout: str,
    late: bool,
    via: str = "enter",
) -> None:
    name = f"after-{lang}-{theme}-{layout}-{via}" + ("-late" if late else "")
    page, errors = open_chat(
        browser,
        lang,
        theme,
        viewport,
        plan(lang, stopDelay=25000 if late else 700),
        name,
    )
    page.click("#chat-input")
    before = len(sent)
    page.click(".atlas-dictation-button")
    wait_text(page, keyword(lang, 0))
    # Enter while the second utterance is still open: it must be sent too.
    page.wait_for_function("() => (window.__dict || {}).open === 1", timeout=10000)
    shot(page, name + "-1-speaking.png", viewport)
    pressed = time.monotonic()
    if via == "enter":
        page.keyboard.press("Enter")
    else:
        page.click("#send-message-button")
    page.wait_for_timeout(150)
    after = state(page)
    check(
        f"{name} Enter stops at once",
        after["state"] in ("finishing", "idle") and after["pressed"] == "false",
        after,
    )
    deadline = time.monotonic() + 15
    while len(sent) == before and time.monotonic() < deadline:
        page.wait_for_timeout(100)
    took = round(time.monotonic() - pressed, 2)
    message = sent[-1] if len(sent) > before else ""
    check(f"{name} one message sent", len(sent) == before + 1, sent[before:])
    if late:
        # The final pass is late: the live text of the open utterance goes after a short wait.
        check(f"{name} sent after the short wait (s)", 5 <= took <= 9, took)
        check(
            f"{name} sent text has the first piece and the open words",
            keyword(lang, 0) in message and SPEECH[lang][1][0][0] in message,
            message,
        )
    else:
        # Measured at the model stub, Open WebUI's own request included; the final words
        # (only in the final text) prove the final pass was used, not the timeout.
        check(f"{name} sent without waiting for the timeout (s)", took < 8, took)
        check(
            f"{name} sent text has both utterances",
            keyword(lang, 0) in message and keyword(lang, 1) in message,
            message,
        )
    check(f"{name} sent text once", message.count(keyword(lang, 0)) == 1, message)
    page.wait_for_timeout(1500)
    shot(page, name + "-2-sent.png", viewport)
    check(
        f"{name} composer empty after sending",
        composer_text(page).strip() == "",
        composer_text(page),
    )
    check(
        f"{name} protocol: stop sent",
        "stop" in page.evaluate("() => window.__dict.sent"),
        page.evaluate("() => window.__dict.sent"),
    )
    check(f"{name} no page error", not errors, errors)
    page.context.close()


def flow_escape(
    browser: Browser, lang: str, theme: str, viewport: ViewportSize, layout: str
) -> None:
    name = f"after-{lang}-{theme}-{layout}-escape"
    page, errors = open_chat(browser, lang, theme, viewport, plan(lang), name)
    before = len(sent)
    page.click(".atlas-dictation-button")
    page.wait_for_function(
        "() => document.querySelector('.atlas-dictation-button').dataset.state === 'live'",
        timeout=10000,
    )
    shot(page, name + "-1-listening.png", viewport)
    wait_text(page, keyword(lang, 0))
    page.wait_for_function("() => (window.__dict || {}).open === 1", timeout=10000)
    page.keyboard.press("Escape")
    page.wait_for_timeout(200)
    after = state(page)
    check(
        f"{name} Escape cancels at once",
        after["state"] == "idle" and after["pressed"] == "false",
        after,
    )
    page.wait_for_timeout(2500)
    text = composer_text(page)
    check(
        f"{name} written piece kept, open utterance dropped",
        keyword(lang, 0) in text and keyword(lang, 1) not in text,
        text,
    )
    check(
        f"{name} no stop sent (no final pass), socket closed",
        "stop" not in page.evaluate("() => window.__dict.sent")
        and page.evaluate("() => window.__dict.closed") == 1,
        page.evaluate("() => window.__dict"),
    )
    check(
        f"{name} live region says cancelled",
        after["status"]
        == ("Dictation cancelled" if lang == "en" else "Dictée annulée"),
        after["status"],
    )
    check(f"{name} nothing sent", len(sent) == before, sent[before:])
    shot(page, name + "-2-cancelled.png", viewport)
    check(f"{name} no horizontal scroll", after["scroll"], after)
    check(f"{name} no page error", not errors, errors)
    page.context.close()


def flow_refused(
    browser: Browser, lang: str, theme: str, viewport: ViewportSize, layout: str
) -> None:
    name = f"after-{lang}-{theme}-{layout}-refused"
    page, errors = open_chat(browser, lang, theme, viewport, plan(lang), name)
    page.evaluate(
        "() => { navigator.mediaDevices.getUserMedia = () => Promise.reject(new DOMException('Permission denied', 'NotAllowedError')); }"
    )
    page.click(".atlas-dictation-button")
    page.wait_for_selector(".atlas-dictation-note:not([hidden])", timeout=10000)
    page.wait_for_timeout(300)
    shot(page, name + ".png", viewport)
    geometry = page.evaluate("""() => { const n = document.querySelector('.atlas-dictation-note').getBoundingClientRect(), e = document.querySelector('#chat-input').getBoundingClientRect(), c = document.querySelector('#message-input-container').getBoundingClientRect();
      return { note: [n.left, n.top, n.right, n.bottom], editor: [e.left, e.top, e.right, e.bottom], composerTop: c.top, width: innerWidth, text: document.querySelector('.atlas-dictation-message').innerText }; }""")
    expected = (
        "Microphone access was refused."
        if lang == "en"
        else "L’accès au micro a été refusé."
    )
    check(
        f"{name} readable sentence",
        geometry["text"].startswith(expected),
        geometry["text"],
    )
    check(
        f"{name} bubble above the composer, not over the text field",
        geometry["note"][3] <= geometry["composerTop"]
        and geometry["note"][0] >= 0
        and geometry["note"][2] <= geometry["width"],
        geometry,
    )
    page.keyboard.press("Escape")
    page.wait_for_selector(".atlas-dictation-note", state="hidden", timeout=5000)
    check(f"{name} Escape closes the bubble", True)
    check(f"{name} no page error", not errors, errors)
    page.context.close()


def before_panel(
    browser: Browser, lang: str, theme: str, viewport: ViewportSize, layout: str
) -> None:
    name = f"before-{lang}-{theme}-{layout}"
    page, errors = open_chat(browser, lang, theme, viewport, plan(lang, gap=4000), name)
    page.click(".atlas-dictation-button")
    page.wait_for_selector(".atlas-dictation-panel:not([hidden])", timeout=10000)
    page.wait_for_function("() => (window.__dict || {}).open === 1", timeout=15000)
    page.wait_for_timeout(300)
    shot(page, name + "-panel.png", viewport)
    check(f"{name} older panel captured", True)
    page.context.close()


def main() -> None:
    args = [
        "--use-fake-device-for-media-stream",
        "--use-fake-ui-for-media-stream",
        "--autoplay-policy=no-user-gesture-required",
    ]
    with shell(), sync_playwright() as play:
        browser = play.chromium.launch(args=args)
        layouts = (
            ("light", DESKTOP, "desktop"),
            ("dark", DESKTOP, "desktop"),
            ("light", PHONE, "phone"),
            ("dark", PHONE, "phone"),
        )
        if MODE == "before":
            for theme, viewport, layout in layouts[:3]:
                before_panel(browser, "en", theme, viewport, layout)
        else:
            only = os.environ.get("DICTATION_UI_ONLY", "")
            for theme, viewport, layout in layouts:
                lang = "en" if layout == "desktop" else "fr"
                if only in ("", "click"):
                    flow_click(browser, lang, theme, viewport, layout)
                if only in ("", "enter") and theme == "dark":
                    flow_enter(browser, lang, theme, viewport, layout, late=False)
                if only in ("", "escape") and theme == "light":
                    flow_escape(browser, lang, theme, viewport, layout)
            if only in ("", "enter"):
                flow_enter(browser, "fr", "light", DESKTOP, "desktop", late=True)
                flow_enter(
                    browser, "en", "light", DESKTOP, "desktop", late=False, via="send"
                )
            if only in ("", "refused"):
                flow_refused(browser, "en", "light", DESKTOP, "desktop")
                flow_refused(browser, "fr", "dark", PHONE, "phone")
        browser.close()
    results["failures"] = failures
    (OUT / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1))
    print(
        f"{sum(1 for c in results['checks'] if c[1])}/{len(results['checks'])} checks passed",
        flush=True,
    )
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
