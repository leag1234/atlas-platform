"""Read-only score endpoint for the staging interface.

GET /api/atlas/quality/scores?chat=<chat id>&ids=<message id>,...  (at most 60 ids)

The browser's Open WebUI session (cookie `token`) is verified by Open WebUI itself, as
the other /api/atlas browser endpoints do; only the answers of chats the person owns are
returned. Listens on 127.0.0.1 only; staging nginx proxies the path (see
nginx-staging.conf).
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from . import source, store

PATH = "/api/atlas/quality/scores"
MAX_IDS = 60
TOKEN_TTL = 120.0
MAX_ATTEMPTS = 3
PUBLIC_STATES = ("scored", "pending", "budget", "failed", "skipped_privacy")


class Auth:
    """Open WebUI session token -> user id, verified by Open WebUI, cached briefly."""

    def __init__(self, base_url: Callable[[], str]) -> None:
        self.base_url = base_url
        self._base: tuple[float, str] = (0.0, "")
        self._cache: dict[str, tuple[float, str]] = {}
        self._lock = threading.Lock()

    def url(self, fresh: bool = False) -> str:
        with self._lock:
            stamp, base = self._base
        if fresh or not base or time.time() - stamp > 300:
            base = self.base_url()
            with self._lock:
                self._base = (time.time(), base)
        return base.rstrip("/") + "/api/v1/auths/" if base else ""

    def user(self, token: str) -> str | None:
        if not token or len(token) > 8192 or any(c in token for c in "\r\n"):
            return None
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self._lock:
            hit = self._cache.get(digest)
            if hit and time.time() - hit[0] < TOKEN_TTL:
                return hit[1]
        data = None
        # The container address changes when the interface restarts: one fresh lookup.
        for fresh in (False, True):
            url = self.url(fresh)
            if not url:
                continue
            req = urllib.request.Request(
                url, headers={"Authorization": "Bearer " + token}
            )
            try:
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read(65536))
                break
            except urllib.error.HTTPError:
                return None
            except (urllib.error.URLError, TimeoutError, ValueError, ConnectionError):
                continue
        user = data.get("id") if isinstance(data, dict) else None
        if not isinstance(user, str) or not user.strip() or len(user) > 200:
            return None
        with self._lock:
            if len(self._cache) > 2000:
                self._cache.clear()
            self._cache[digest] = (time.time(), user)
        return user


def public(record: dict[str, Any]) -> dict[str, Any]:
    state = str(record["state"])
    if state == "evidence_missing":
        # Retried while attempts remain; then shown as unavailable, never as a score.
        state = (
            "failed" if int(record.get("attempts") or 0) >= MAX_ATTEMPTS else "pending"
        )
    if state not in PUBLIC_STATES:
        return {"state": "none"}
    out: dict[str, Any] = {"state": state}
    if state == "scored":
        out.update(
            score=record["score"],
            verdict=record["verdict"],
            worst=record["worst"],
            classes=json.loads(record["classes"] or "[]"),
        )
    return out


def answer_states(
    scores: store.Store,
    webui_db: Any,
    user: str,
    chat: str,
    ids: list[str],
    epoch: float,
) -> dict[str, dict[str, Any]]:
    db = source.connect_ro(webui_db)
    try:
        known = source.lookup(db, chat, ids)
    finally:
        db.close()
    owned = [mid for mid, (owner, _done, _created) in known.items() if owner == user]
    stored = scores.many(owned)
    out: dict[str, dict[str, Any]] = {}
    for mid in owned:
        _owner, done, created = known[mid]
        if mid in stored:
            out[mid] = public(stored[mid])
        elif not done:
            out[mid] = {"state": "running"}
        elif created >= epoch:
            out[mid] = {"state": "pending"}
        else:
            out[mid] = {"state": "none"}
    return out


def handler(
    scores: store.Store, webui_db: Any, auth: Auth, epoch: Callable[[], float]
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "atlas-live-judge"
        sys_version = ""

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        def _send(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "private, no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            url = urllib.parse.urlsplit(self.path)
            if url.path == "/api/atlas/quality/health":
                self._send(200, {"ok": True})
                return
            if url.path != PATH:
                self._send(404, {"error": "not_found"})
                return
            site = self.headers.get("Sec-Fetch-Site")
            if site and site not in ("same-origin", "none"):
                self._send(403, {"error": "cross_site"})
                return
            token = ""
            try:
                cookie = SimpleCookie(self.headers.get("Cookie", ""))
                if "token" in cookie:
                    token = cookie["token"].value
            except CookieError:
                token = ""
            user = auth.user(token)
            if user is None:
                self._send(401, {"error": "unauthenticated"})
                return
            query = urllib.parse.parse_qs(url.query)
            chat = (query.get("chat") or [""])[0]
            ids = [i for i in (query.get("ids") or [""])[0].split(",") if i][:MAX_IDS]
            if not source.CHAT_ID.fullmatch(chat) or not all(
                source.MESSAGE_ID.fullmatch(i) for i in ids
            ):
                self._send(422, {"error": "invalid_request"})
                return
            states = answer_states(scores, webui_db, user, chat, ids, epoch())
            self._send(200, {"messages": states})

    return Handler


def serve(
    scores: store.Store,
    webui_db: Any,
    webui_url: Callable[[], str],
    port: int,
    epoch: Callable[[], float],
) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer(
        ("127.0.0.1", port), handler(scores, webui_db, Auth(webui_url), epoch)
    )
    httpd.daemon_threads = True
    return httpd
