"""Open one app file in headless Chromium and report what a person would hit.

Runs on its own (standard library and Playwright only), inside the render
container or, for development, on the host: services/orchestrator/app_render.py
passes this file's source to ``python -I -c``, the settings as the first
argument (JSON) and the HTML file on standard input. It prints one JSON object
per line: {"stage": "launched"}, {"stage": "loaded"}, then {"result": {...}}.

The page is served at a fixed https address by request interception with the
side view's headers (CSP with sandbox, Permissions-Policy); every other request
is aborted and recorded. Nothing reaches the network.
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any

APP_URL = "https://app.atlas-render.invalid/app.html"
BUTTONS = "button, [role=button], input[type=button], input[type=submit]"
MAX_ITEMS = 8
MAX_TEXT = 160

# The page records its own policy violations, listeners and sound starts.
# A sound started before the person's first press or key is blocked by the
# browser's autoplay rule ("The AudioContext was not allowed to start"): the
# check's own evaluate() calls count as a gesture for the browser, so the page
# notes trusted presses itself.
INIT_SCRIPT = """
(() => {
  const s = (window.__atlasRender = { violations: [], listeners: {}, gesture: false, audio: [], sounds: 0 });
  // A fixed sequence for Math.random: the same file shows the same states
  // (questions, levels) at each check, so a repair is judged on the same run.
  let seed = 0x2f6b4c1d;
  Math.random = () => {
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  for (const type of ['pointerdown', 'mousedown', 'keydown', 'touchstart', 'click']) {
    window.addEventListener(type, (e) => { if (e.isTrusted) s.gesture = true; }, true);
  }
  const early = (what) => {
    if (!s.gesture && s.audio.length < 4 && !s.audio.includes(what)) s.audio.push(what);
  };
  for (const name of ['AudioContext', 'webkitAudioContext']) {
    const Original = window[name];
    if (typeof Original !== 'function') continue;
    const Wrapped = function (...args) {
      early(name);
      return new Original(...args);
    };
    Wrapped.prototype = Original.prototype;
    try { Object.defineProperty(window, name, { value: Wrapped, configurable: true, writable: true }); } catch (e) {}
  }
  // Sounds started, for the checks of the expectations ledger ("sound").
  try {
    const Source = window.AudioScheduledSourceNode && AudioScheduledSourceNode.prototype;
    if (Source) {
      const start = Source.start;
      Source.start = function (...args) { s.sounds += 1; return start.apply(this, args); };
    }
  } catch (e) {}
  const play = HTMLMediaElement.prototype.play;
  HTMLMediaElement.prototype.play = function (...args) {
    s.sounds += 1;
    if (!this.muted) early(this.tagName.toLowerCase() + '.play()');
    return play.apply(this, args);
  };
  document.addEventListener('securitypolicyviolation', (e) => {
    s.violations.push({
      directive: String(e.effectiveDirective || e.violatedDirective || ''),
      uri: String(e.blockedURI || '').slice(0, 120),
    });
  }, true);
  const add = EventTarget.prototype.addEventListener;
  EventTarget.prototype.addEventListener = function (type, fn, options) {
    try { s.listeners[type] = (s.listeners[type] || 0) + 1; } catch (e) {}
    return add.call(this, type, fn, options);
  };
})();
"""

INSPECT = """
() => {
  const s = window.__atlasRender || { violations: [], listeners: {} };
  const shown = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2 || r.bottom <= 0 || r.right <= 0) return false;
    if (r.left >= innerWidth) return false;
    const st = getComputedStyle(el);
    return st.visibility !== 'hidden' && st.display !== 'none' && Number(st.opacity) > 0.05;
  };
  const body = document.body;
  let visible = false;
  if (body) {
    const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT);
    while (!visible && walker.nextNode()) {
      const node = walker.currentNode;
      visible = !!(node.textContent.trim() && node.parentElement && shown(node.parentElement));
    }
    const all = body.querySelectorAll('*');
    for (let i = 0; !visible && i < all.length && i < 5000; i++) {
      const el = all[i];
      const tag = el.tagName.toLowerCase();
      if (['canvas', 'svg', 'img', 'video', 'input', 'select', 'textarea', 'button', 'progress', 'meter'].includes(tag)) {
        visible = shown(el);
        continue;
      }
      const st = getComputedStyle(el);
      const painted = (st.backgroundColor !== 'rgba(0, 0, 0, 0)' && st.backgroundColor !== 'transparent')
        || st.backgroundImage !== 'none' || parseFloat(st.borderTopWidth) > 0;
      if (painted) {
        const r = el.getBoundingClientRect();
        visible = r.width >= 16 && r.height >= 16 && shown(el);
      }
    }
  }
  const events = ['click', 'pointerdown', 'pointerup', 'mousedown', 'mouseup', 'touchstart',
    'touchend', 'submit', 'change', 'input', 'keydown', 'keyup', 'keypress'];
  let handlers = events.some((t) => s.listeners[t] > 0);
  const props = events.map((t) => 'on' + t);
  if (!handlers) {
    const targets = [window, document, ...(body ? body.querySelectorAll('*') : [])];
    handlers = targets.some((el) => props.some((p) => typeof el[p] === 'function'));
  }
  const buttons = body ? [...body.querySelectorAll(%s)].filter(shown).length : 0;
  return { visible, buttons, handlers, violations: s.violations.slice(0, 20) };
}
""" % json.dumps(BUTTONS)

# Every <img> of the page, shown or not: one without a source, or whose source
# did not decode, is a broken picture in the side view.
IMAGES = """
() => [...document.images].map((img) => {
  const src = (img.getAttribute('src') || '').trim();
  const srcset = (img.getAttribute('srcset') || '').trim();
  const name = img.id ? '#' + img.id : (img.alt ? 'alt="' + img.alt.slice(0, 40) + '"' : 'img');
  if (!src && !srcset) return { name, src: '', reason: 'empty' };
  if (img.complete && img.naturalWidth === 0) return { name, src: src.slice(0, 60), reason: 'unloadable' };
  return null;
}).filter(Boolean).slice(0, 8)
"""
AUDIO = "() => ((window.__atlasRender || {}).audio || []).slice(0, 4)"

# The checks of the expectations ledger (services/orchestrator/expectations.py):
# find the element a requirement names (by a pinned id, else by role and
# words in its label, text, id, title or aria-label), act on it, and compare
# what the page shows, stores and plays before and after.
MAX_ASSERTIONS = 24
# An element that only shows in a later state of the app (a replay button at
# the end of a game, a panel a press opens, a message after a countdown) is
# not missing when the page opens: the check plays the app forward to reach
# it, pressing its visible controls in turn and running its clock, at most
# REACH_STEPS steps and REACH_MS of real time. Staging -151: a quiz's
# "Replay" button, hidden until the last question, was reported as not
# working in three versions that had it.
REACH_STEPS = 60
REACH_MS = 2500
# Page time a step runs after a press (the next question shows), and when
# nothing can be pressed (a countdown runs out).
REACH_PRESS_MS = 1500
REACH_IDLE_MS = 5000
PRESS = r"""
(step) => {
  const target = window.__atlasTarget;
  const shown = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    const st = getComputedStyle(el);
    return st.visibility !== 'hidden' && st.display !== 'none' && Number(st.opacity) > 0.05;
  };
  const controls = [...document.querySelectorAll(
    'button,[role=button],input[type=button],input[type=submit]'
  )].slice(0, 400).filter((el) => el !== target && !el.disabled && shown(el));
  if (!controls.length) return false;
  controls[step % controls.length].click();
  return true;
}
"""
FIND = r"""
(spec) => {
  const fold = (t) => String(t || '').normalize('NFKD').replace(/[̀-ͯ]/g, '')
    .toLowerCase().replace(/[^\p{L}\p{N}]+/gu, ' ').trim();
  const ROLES = {
    button: 'button,[role=button],input[type=button],input[type=submit],summary',
    slider: 'input[type=range],[role=slider],input[type=number]',
    input: 'input:not([type=button]):not([type=submit]):not([type=hidden]),textarea,select,[contenteditable=true]',
    select: 'select,[role=listbox],[role=radiogroup]',
    drawing: 'canvas,svg',
    any: 'body *',
  };
  const shown = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    const st = getComputedStyle(el);
    return st.visibility !== 'hidden' && st.display !== 'none' && Number(st.opacity) > 0.05;
  };
  const text = (node, limit) => {
    const t = node ? (node.textContent || '') : '';
    return t.length <= limit ? t : '';
  };
  const names = (el) => {
    const parts = ['id', 'aria-label', 'title', 'placeholder', 'name', 'value', 'alt']
      .map((a) => el.getAttribute(a));
    parts.push(text(el, 120));
    if (el.id) {
      for (const label of document.querySelectorAll('label[for="' + CSS.escape(el.id) + '"]')) parts.push(label.textContent);
    }
    const label = el.closest('label');
    if (label) parts.push(text(label, 120));
    const by = el.getAttribute('aria-labelledby');
    if (by) for (const id of by.split(/\s+/)) parts.push(text(document.getElementById(id), 120));
    parts.push(text(el.previousElementSibling, 60));
    if (el.parentElement && el.parentElement !== document.body) parts.push(text(el.parentElement, 80));
    return fold(parts.filter(Boolean).join(' '));
  };
  const words = (spec.words || []).map(fold).filter(Boolean);
  const pool = [];
  if (spec.bound) {
    const el = document.getElementById(spec.bound);
    if (el) pool.push(el);
  }
  const all = [...document.querySelectorAll(ROLES[spec.role] || ROLES.any)].slice(0, 4000);
  const matching = all.filter((el) => !words.length || words.some((w) => names(el).includes(w)));
  if (spec.role === 'any' || !spec.role) {
    // The innermost element first, not the container around it.
    matching.sort((a, b) => (a.textContent || '').length - (b.textContent || '').length);
  }
  pool.push(...matching);
  const el = pool.find(shown) || pool[0];
  if (!el) return null;
  window.__atlasTarget = el;
  return { id: el.id || '', visible: shown(el), tag: el.tagName.toLowerCase() };
}
"""
SET_VALUE = r"""
() => {
  const t = window.__atlasTarget;
  if (!t) return false;
  const type = String(t.type || '').toLowerCase();
  if (t.tagName === 'SELECT') {
    if (t.options.length > 1) t.selectedIndex = (t.selectedIndex + 1) % t.options.length;
  } else if (type === 'checkbox' || type === 'radio') {
    t.click();
    return true;
  } else if (type === 'range' || type === 'number') {
    const min = t.min !== '' ? Number(t.min) : 0;
    const max = t.max !== '' ? Number(t.max) : 100;
    const now = Number(t.value || min);
    let next = now + (max - min) / 4;
    if (next > max) next = now - (max - min) / 4;
    t.value = String(Math.round(next));
  } else if (type === 'color') {
    t.value = t.value === '#ff0000' ? '#0000ff' : '#ff0000';
  } else if ('value' in t) {
    t.value = String(t.value || '') + '1';
  } else if (t.isContentEditable) {
    t.textContent += '1';
  } else if (t.getAttribute('role') === 'slider') {
    t.focus();
    return 'key';
  }
  t.dispatchEvent(new Event('input', { bubbles: true }));
  t.dispatchEvent(new Event('change', { bubbles: true }));
  return true;
}
"""
# What the page shows (text, classes, styles, values but the acted-on
# element's own, drawings), stores and plays.
FINGERPRINT = r"""
() => {
  const s = window.__atlasRender || {};
  const target = window.__atlasTarget;
  let h = 0;
  const add = (t) => { t = String(t); for (let i = 0; i < t.length; i++) h = (Math.imul(h, 31) + t.charCodeAt(i)) | 0; };
  const body = document.body;
  if (body) {
    add(body.innerText.slice(0, 20000));
    const all = body.querySelectorAll('*');
    for (let i = 0; i < all.length && i < 4000; i++) {
      const e = all[i];
      const c = e.getAttribute('class');
      if (c) add(c);
      const st = e.getAttribute('style');
      if (st) add(st);
      if (e !== target && typeof e.value === 'string') add(e.value);
      if (e !== target && e.checked) add('checked');
      if (e.tagName === 'CANVAS') { try { add(e.toDataURL()); } catch (x) {} }
    }
  }
  let store = '';
  for (const name of ['localStorage', 'sessionStorage']) {
    try {
      const area = window[name];
      for (let i = 0; i < area.length; i++) { const k = area.key(i); store += name + ':' + k + '=' + area.getItem(k) + ';'; }
    } catch (x) {}
  }
  return { page: h, store: store.slice(0, 20000), sounds: s.sounds || 0 };
}
"""
# The main colours of the page, by painted area.
PALETTE = r"""
() => {
  const area = {};
  const add = (c, a) => { if (!c || c === 'rgba(0, 0, 0, 0)' || c === 'transparent' || c === 'none') return; area[c] = (area[c] || 0) + a; };
  const page = innerWidth * innerHeight;
  if (document.body) {
    add(getComputedStyle(document.documentElement).backgroundColor, page);
    add(getComputedStyle(document.body).backgroundColor, page);
    const all = document.body.querySelectorAll('*');
    for (let i = 0; i < all.length && i < 3000; i++) {
      const e = all[i];
      const r = e.getBoundingClientRect();
      if (r.width < 2 || r.height < 2) continue;
      const st = getComputedStyle(e);
      if (st.display === 'none' || st.visibility === 'hidden') continue;
      const a = Math.min(r.width * r.height, page);
      add(st.backgroundColor, a);
      if (e instanceof SVGElement) add(st.fill, a / 2);
      if ([...e.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim())) add(st.color, a / 10);
    }
  }
  return Object.entries(area).sort((x, y) => y[1] - x[1]).slice(0, 6).map((x) => x[0]);
}
"""


def emit(payload: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def short(text: object) -> str:
    return " ".join(str(text).split())[:MAX_TEXT]


def reach(
    page: Any, spec: dict[str, Any], clock: bool, run_later: Any, budget_ms: int
) -> tuple[dict[str, Any] | None, int]:
    """Play the app forward until the element shows (REACH_STEPS, REACH_MS).

    Each step presses the next visible control (in turn, never the element
    itself) and runs the page's clock. Returns what FIND found last and the
    number of steps that showed the element (0: it never showed).
    """
    ends = time.monotonic() + max(0, min(REACH_MS, budget_ms)) / 1000
    found: dict[str, Any] | None = None
    for step in range(REACH_STEPS):
        if time.monotonic() > ends:
            break
        pressed = bool(page.evaluate(PRESS, step))
        if clock:
            run_later(REACH_PRESS_MS if pressed else REACH_IDLE_MS)
        elif pressed:
            page.wait_for_timeout(60)
        else:
            # Nothing to press and no clock to run: no later state is reachable.
            break
        value = page.evaluate(FIND, spec)
        found = value if isinstance(value, dict) else None
        if found is not None and found.get("visible"):
            return found, step + 1
    return found, 0


def run_assertion(
    page: Any,
    spec: dict[str, Any],
    clock: bool,
    run_later: Any,
    reach_ms: int = 0,
) -> dict[str, Any]:
    """One requirement check: find the element, act on it, compare the page.

    reach_ms: when the element is missing or hidden as the page opens, the
    time to play the app forward to a state that shows it (on a page of its
    own). "explored" then says it was tried and "reached" after how many
    steps; an element still not shown is never acted on.
    """
    from playwright.sync_api import Error

    entry: dict[str, Any] = {
        "id": str(spec.get("id") or "")[:16],
        "found": False,
        "visible": False,
        "matched_id": "",
        "met": [],
    }
    try:
        found = page.evaluate(FIND, spec)
        if not (isinstance(found, dict) and found.get("visible")) and reach_ms > 0:
            entry["explored"] = True
            later, steps = reach(page, spec, clock, run_later, reach_ms)
            if later is not None:
                found = later
            if steps:
                entry["reached"] = steps
    except Error as error:
        entry["error"] = short(error)[:80]
        return entry
    if not isinstance(found, dict):
        return entry
    entry.update(
        found=True,
        visible=bool(found.get("visible")),
        matched_id=str(found.get("id") or "")[:60],
    )
    expects = [name for name in spec.get("expect") or [] if isinstance(name, str)]
    if entry["visible"] and "present" in expects:
        entry["met"].append("present")
    if not any(name != "present" for name in expects) or not entry["visible"]:
        return entry
    action = spec.get("action")
    try:
        before = page.evaluate(FINGERPRINT)
        if action == "click":
            handle = page.evaluate_handle("() => window.__atlasTarget").as_element()
            if handle is not None:
                handle.click(timeout=1000, no_wait_after=True)
        elif action == "set":
            if page.evaluate(SET_VALUE) == "key":
                page.keyboard.press("ArrowRight")
        elif action == "key":
            page.evaluate(
                "() => { const t = window.__atlasTarget; if (t && t.focus) t.focus(); }"
            )
            page.keyboard.press(str(spec.get("key") or "Space"))
        if clock:
            run_later(1500)
        page.wait_for_timeout(150)
        after = page.evaluate(FINGERPRINT)
    except Error as error:
        entry["error"] = short(error)[:80]
        return entry
    if after.get("page") != before.get("page"):
        entry["met"].append("changes")
    if after.get("store") != before.get("store"):
        entry["met"].append("stores")
    if int(after.get("sounds") or 0) > int(before.get("sounds") or 0):
        entry["met"].append("sound")
    return entry


def main() -> int:
    settings: dict[str, Any] = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    document = sys.stdin.buffer.read()
    headers = {
        "Content-Type": "text/html; charset=utf-8",
        "Content-Security-Policy": str(settings.get("csp", "")),
        "Permissions-Policy": str(settings.get("permissions", "")),
        "Cache-Control": "no-store",
    }
    load_ms = int(settings.get("load_ms", 6000))
    settle_ms = int(settings.get("settle_ms", 800))
    clicks = int(settings.get("clicks", 3))
    # Timers run forward on the page's clock (a countdown, a turn timer, a
    # sound at the end of a question): what happens after a while with nobody
    # touching the page is checked too, in little real time.
    later_ms = int(settings.get("later_ms", 0))
    later_budget_ms = int(settings.get("later_budget_ms", 2500))
    from playwright.sync_api import ConsoleMessage, Error, Route, sync_playwright

    errors: list[dict[str, str]] = []
    blocked: list[str] = []
    refused: list[str] = []
    phase = {"name": "load"}
    started = time.monotonic()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--mute-audio",
                "--js-flags=--max-old-space-size=192",
            ],
        )
        emit({"stage": "launched"})

        def console(message: ConsoleMessage) -> None:
            text = short(message.text)
            if message.type == "error" and text.startswith("Refused"):
                refused.append(text)

        def route(request_route: Route) -> None:
            url = request_route.request.url
            if url == APP_URL:
                request_route.fulfill(status=200, headers=headers, body=document)
                return
            blocked.append(short(url)[:120])
            request_route.abort()

        def open_page() -> Any:
            opened = browser.new_page(viewport={"width": 480, "height": 720})
            opened.set_default_timeout(2000)
            opened.add_init_script(INIT_SCRIPT)
            if later_ms > 0:
                opened.clock.install()
            opened.on(
                "pageerror",
                lambda error: errors.append(
                    {"phase": phase["name"], "message": short(error)}
                ),
            )
            opened.on("console", console)
            opened.on("dialog", lambda dialog: dialog.dismiss())
            opened.route("**/*", route)
            return opened

        page = open_page()
        loaded = True
        try:
            page.goto(APP_URL, wait_until="load", timeout=load_ms)
        except Error as error:
            loaded = False
            errors.append({"phase": "load", "message": "not_loaded: " + short(error)})
        emit({"stage": "loaded"})
        page.wait_for_timeout(settle_ms)
        state: dict[str, Any] = page.evaluate(INSPECT) if loaded else {}
        images: list[dict[str, str]] = []

        def note_images() -> None:
            for image in page.evaluate(IMAGES):
                if isinstance(image, dict) and image not in images:
                    images.append(image)

        def run_later(step: int, target: Any = None) -> None:
            try:
                (target or page).clock.run_for(step)
            except Error as error:
                # A timer callback that throws surfaces here, not as a
                # pageerror: the fake clock calls it from evaluate().
                first = str(error).split("\n", 1)[0]
                message = first.removeprefix("Clock.run_for: ")
                entry = {"phase": "later", "message": short(message)}
                if entry not in errors and len(errors) < MAX_ITEMS:
                    errors.append(entry)

        if loaded:
            note_images()
        palette: list[str] = []
        if loaded and settings.get("palette"):
            try:
                palette = [str(colour) for colour in page.evaluate(PALETTE)][:6]
            except Error:
                palette = []

        def fresh_assertion(spec: dict[str, Any], reach_ms: int) -> dict[str, Any]:
            """An interaction on a page of its own, as the person opens it:
            what an earlier check started (a sequencer playing) cannot make
            a later one pass, and playing the app forward to reach an element
            leaves the page the render check reports on untouched."""
            extra = open_page()
            try:
                extra.goto(APP_URL, wait_until="load", timeout=load_ms)
                extra.wait_for_timeout(min(settle_ms, 300))
                return run_assertion(
                    extra,
                    spec,
                    later_ms > 0,
                    lambda step: run_later(step, extra),
                    reach_ms,
                )
            except Error as error:
                return {
                    "id": str(spec.get("id") or "")[:16],
                    "found": False,
                    "visible": False,
                    "matched_id": "",
                    "met": [],
                    "error": short(error)[:80],
                }
            finally:
                try:
                    extra.close()
                except Error:
                    pass

        checks: list[dict[str, Any]] = []
        specs = settings.get("assertions") or []
        if loaded and isinstance(specs, list):
            phase["name"] = "click"
            ends = time.monotonic() + int(settings.get("assert_budget_ms", 6000)) / 1000
            for spec in specs[:MAX_ASSERTIONS]:
                if not isinstance(spec, dict) or time.monotonic() > ends:
                    # Not run: the ledger checks it another way (expectations.py).
                    continue
                before = len(errors)
                acts = any(name != "present" for name in spec.get("expect") or [])
                left_ms = int((ends - time.monotonic()) * 1000)
                entry = (
                    fresh_assertion(spec, left_ms)
                    if acts
                    else run_assertion(page, spec, later_ms > 0, run_later)
                )
                if not acts and not entry.get("visible") and "error" not in entry:
                    # Not shown as the page opens: played forward on a page
                    # of its own.
                    entry = fresh_assertion(spec, left_ms)
                checks.append(entry)
                for entry in errors[before:]:
                    entry.setdefault("control", " ".join(spec.get("words") or [])[:40])
            phase["name"] = "load"
        waited = 0
        if loaded and later_ms > 0:
            phase["name"] = "later"
            spent = time.monotonic()
            while (
                waited < later_ms
                and (time.monotonic() - spent) * 1000 < later_budget_ms
            ):
                step = min(1000, later_ms - waited)
                run_later(step)
                waited += step
            note_images()
        clicked: list[str] = []
        if loaded and clicks > 0:
            phase["name"] = "click"
            for handle in page.query_selector_all(BUTTONS)[: clicks * 3]:
                if len(clicked) >= clicks:
                    break
                try:
                    if not handle.is_visible() or not handle.is_enabled():
                        continue
                    label = short(handle.inner_text() or handle.get_attribute("value"))
                    before = len(errors)
                    handle.click(timeout=1000, no_wait_after=True)
                    page.wait_for_timeout(200)
                    clicked.append(label)
                    for entry in errors[before:]:
                        entry["control"] = label[:40]
                except Error:
                    continue
            if clicked:
                if later_ms > 0:
                    # What the presses scheduled (the next question) is shown.
                    run_later(2000)
                final = page.evaluate("() => (window.__atlasRender||{}).violations||[]")
                state["violations"] = final[:20]
                note_images()
        audio = list(page.evaluate(AUDIO)) if loaded else []
        browser.close()
    emit(
        {
            "result": {
                "loaded": loaded,
                "visible": bool(state.get("visible")),
                "buttons": int(state.get("buttons") or 0),
                "handlers": bool(state.get("handlers")),
                "errors": errors[:MAX_ITEMS],
                "violations": list(state.get("violations") or [])[:MAX_ITEMS],
                "refused": refused[:MAX_ITEMS],
                "blocked": blocked[:MAX_ITEMS],
                "clicked": clicked,
                "images": images[:MAX_ITEMS],
                "audio": audio,
                "assertions": checks,
                "palette": palette,
                "waited_ms": waited,
                "ms": int((time.monotonic() - started) * 1000),
            }
        }
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
