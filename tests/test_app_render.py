"""Render check of side-panel apps: Atlas opens the app before saying it is done.

Staging 2026-11-20: "Check the app" was static (block, parse, no network
API), so an app that threw at load or rendered nothing passed and was handed
over. The browser fixtures run the real check script on the host's Playwright
(ATLAS_APP_RENDER=local) when a Chromium is installed, and in the
atlas-app-render container when that image exists; both are skipped
otherwise. The app-turn tests script the check.
"""

import os
import asyncio
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from packages.user_errors import app_render_failure
from services.orchestrator import app_render, artifact_store
from services.orchestrator.app_render import RenderProblem, RenderReport
from services.orchestrator.app_turn import process_app_turn
from services.orchestrator.chat_schema import ChatRequest
from services.orchestrator.interactions import Interaction
from services.orchestrator.stream_client import sink_context
from test_app_turn import APP, OWNER, FakeModel, answer


def page(body: str, script: str = "") -> str:
    return (
        '<!DOCTYPE html>\n<html lang="en">\n<head><title>Fixture</title></head>\n'
        f"<body>\n{body}\n<script>\n{script}\n</script>\n</body>\n</html>"
    )


GOOD = page(
    '<button id="add">Add</button><p id="count">0</p>',
    "let n = 0; let ctx = null;\n"
    "document.getElementById('add').addEventListener('click', () => {\n"
    "  ctx = ctx || new AudioContext();\n"
    "  const tone = ctx.createOscillator(); tone.connect(ctx.destination);\n"
    "  tone.start(); tone.stop(ctx.currentTime + 0.05);\n"
    "  try { localStorage.setItem('n', String(++n)); } catch (e) {}\n"
    "  document.getElementById('count').textContent = String(n);\n"
    "});",
)
BLANK = page('<div id="app"></div>', "const ready = true;")
THROWS = page(
    "<h1>Snake</h1>",
    "document.getElementById('board').addEventListener('click', () => {});",
)
CSP = page(
    "<h1>Drum machine</h1>",
    "fetch('https://samples.example/kick.wav').catch(() => {});",
)
DEAD = page("<button>Start</button><button>Stop</button>")
CLICK_ERROR = page(
    '<button id="play">Play</button>',
    "document.getElementById('play').onclick = () => { playSample(); };",
)


def _local_browser() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            browser.close()
    except Exception:  # noqa: BLE001 - any failure means no usable browser
        return False
    return True


def _docker_image() -> bool:
    if shutil.which("docker") is None:
        return False
    result = subprocess.run(
        ["docker", "image", "inspect", app_render.DEFAULT_IMAGE],
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


class BrowserFixtures(unittest.IsolatedAsyncioTestCase):
    """The same fixtures for each runtime (this base class runs none)."""

    mode = ""

    def setUp(self) -> None:
        if not self.mode:
            self.skipTest("runtime chosen by the subclasses")
        environment = patch.dict(os.environ, {"ATLAS_APP_RENDER": self.mode})
        environment.start()
        self.addCleanup(environment.stop)
        app_render._semaphore = None
        app_render.forget()

    async def report(self, document: str) -> RenderReport:
        return await app_render.check(document)

    async def test_a_working_app_passes(self) -> None:
        report = await self.report(GOOD)
        self.assertEqual(report.status, "ok", report.problems)
        self.assertLess(report.ms, app_render.CHECK_SECONDS * 1000)

    async def test_a_blank_app_fails(self) -> None:
        report = await self.report(BLANK)
        self.assertEqual(report.codes, ["blank_page"])

    async def test_an_app_that_throws_at_load_fails(self) -> None:
        report = await self.report(THROWS)
        self.assertEqual(report.codes, ["page_error"])
        self.assertIn("addEventListener", report.problems[0].detail)

    async def test_an_app_that_needs_the_network_fails_on_the_policy(self) -> None:
        report = await self.report(CSP)
        self.assertEqual(report.codes, ["csp_violation"])
        self.assertIn("connect-src", report.problems[0].detail)
        self.assertIn("samples.example", report.problems[0].detail)

    async def test_buttons_without_any_handler_fail(self) -> None:
        report = await self.report(DEAD)
        self.assertEqual(report.codes, ["dead_controls"])

    async def test_a_button_that_throws_when_pressed_fails(self) -> None:
        report = await self.report(CLICK_ERROR)
        self.assertEqual(report.codes, ["control_error"])
        self.assertIn('"Play"', app_render.repair_lines(report))
        self.assertIn("playSample", app_render.repair_lines(report))


@unittest.skipUnless(_local_browser(), "no Playwright Chromium on this host")
class LocalBrowserTests(BrowserFixtures):
    mode = "local"


@unittest.skipUnless(_docker_image(), "atlas-app-render image not built")
class ContainerBrowserTests(BrowserFixtures):
    mode = "docker"


class CheckTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        app_render._semaphore = None
        app_render.forget()

    async def test_off_by_default(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ATLAS_APP_RENDER", None)
            report = await app_render.check(GOOD)
        self.assertEqual((report.status, report.reason), ("skipped", "off"))

    async def test_no_check_without_time_for_it(self) -> None:
        with patch.dict(os.environ, {"ATLAS_APP_RENDER": "docker"}):
            report = await app_render.check(GOOD, app_render.MINIMUM_SECONDS - 1)
        self.assertEqual((report.status, report.reason), ("skipped", "deadline"))

    async def test_a_missing_runtime_is_unavailable_not_a_failure(self) -> None:
        with (
            patch.dict(
                os.environ, {"ATLAS_APP_RENDER": "docker", "PATH": "/nonexistent"}
            ),
        ):
            report = await app_render.check(GOOD)
        self.assertEqual(report.status, "unavailable")
        self.assertEqual(report.problems, [])

    async def test_a_silent_runtime_is_unavailable(self) -> None:
        with (
            patch.dict(os.environ, {"ATLAS_APP_RENDER": "docker"}),
            patch.object(
                app_render, "_run", AsyncMock(return_value=(["launched"], None))
            ),
        ):
            report = await app_render.check(GOOD)
        self.assertEqual(report.status, "unavailable")

    async def test_a_file_is_opened_once(self) -> None:
        # Staging -145: a repair that writes the same file again, or the stored
        # version a follow-up starts from, is not opened a second time.
        result = {"loaded": True, "visible": True, "buttons": 0, "handlers": True}
        run = AsyncMock(return_value=(["launched", "loaded", "result"], result))
        with (
            patch.dict(os.environ, {"ATLAS_APP_RENDER": "docker"}),
            patch.object(app_render, "_run", run),
        ):
            first = await app_render.check(GOOD)
            again = await app_render.check(GOOD)
            other = await app_render.check(BLANK)
        self.assertEqual(run.await_count, 2)
        self.assertEqual(
            (first.status, again.status, again.reason), ("ok", "ok", "remembered")
        )
        self.assertEqual(again.ms, 0.0)
        self.assertEqual(other.status, "ok")
        app_render.remember_version("a1", 3, first)
        kept = app_render.version_report("a1", 3)
        self.assertEqual(kept.status if kept else None, "ok")
        self.assertIsNone(app_render.version_report("a1", 4))

    async def test_a_page_that_hangs_after_loading_does_not_respond(self) -> None:
        with (
            patch.dict(os.environ, {"ATLAS_APP_RENDER": "docker"}),
            patch.object(
                app_render,
                "_run",
                AsyncMock(return_value=(["launched", "loaded"], None)),
            ),
        ):
            report = await app_render.check(GOOD)
        self.assertEqual(report.codes, ["not_responding"])

    async def test_the_check_runs_with_the_side_view_policy(self) -> None:
        from services.orchestrator.artifact_routes import PREVIEW_CSP

        config = app_render.settings()
        self.assertEqual(config["csp"], PREVIEW_CSP + "'none'")
        self.assertIn("connect-src 'none'", str(config["csp"]))
        self.assertIn("sandbox allow-scripts", str(config["csp"]))
        # The storage shim the side view inserts is part of what is opened.
        self.assertIn(b"localStorage", app_render.served(GOOD))

    def test_the_container_has_no_network_and_is_capped(self) -> None:
        with patch.dict(os.environ, {"ATLAS_APP_RENDER": "docker"}):
            command = app_render._command("atlas-app-render-x", "pass", "{}")
        joined = " ".join(command)
        for flag in (
            "--network none",
            "--pull never",
            "--read-only",
            "--cap-drop ALL",
            "--memory 512m",
            "--pids-limit 256",
            "no-new-privileges",
        ):
            self.assertIn(flag, joined)

    async def test_a_cancelled_check_removes_its_container(self) -> None:
        remove = AsyncMock()
        process = AsyncMock()
        process.wait = AsyncMock()
        with (
            patch.dict(os.environ, {"ATLAS_APP_RENDER": "docker"}),
            patch.object(app_render, "_remove", remove),
            patch(
                "services.orchestrator.app_render.asyncio.create_subprocess_exec",
                AsyncMock(return_value=process),
            ),
            patch(
                "services.orchestrator.app_render.asyncio.wait_for",
                AsyncMock(side_effect=asyncio.CancelledError()),
            ),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await app_render._run(GOOD.encode(), 1, {})
        remove.assert_awaited_once()

    async def test_the_reaper_removes_only_old_labelled_containers(self) -> None:
        now = datetime.now(timezone.utc)
        old = now - timedelta(seconds=app_render.CONTAINER_SECONDS + 60)
        young = now - timedelta(seconds=app_render.CONTAINER_SECONDS - 60)
        plain = now - timedelta(seconds=app_render.CONTAINER_SECONDS + 120)

        class Process:
            returncode = 0

            async def communicate(self) -> tuple[bytes, bytes]:
                # Docker's own format: offset then zone name.
                return (
                    (
                        f"old {old.strftime('%Y-%m-%d %H:%M:%S %z')} UTC\n"
                        f"young {young.strftime('%Y-%m-%d %H:%M:%S %z')} UTC\n"
                        f"plain {plain.strftime('%Y-%m-%d %H:%M:%S %z')} UTC\n"
                    ).encode("utf-8"),
                    b"",
                )

            async def wait(self) -> int:
                return 0

        remove = AsyncMock()
        with (
            patch.object(app_render, "_remove", remove),
            patch(
                "services.orchestrator.app_render.asyncio.create_subprocess_exec",
                AsyncMock(return_value=Process()),
            ),
        ):
            await app_render.reap_orphans()
        self.assertEqual(
            [call.args for call in remove.await_args_list],
            [("old",), ("plain",)],
        )

    def test_problems_are_ordered_and_deduplicated(self) -> None:
        found = app_render.problems(
            {
                "loaded": True,
                "visible": False,
                "buttons": 2,
                "handlers": False,
                "errors": [
                    {"phase": "load", "message": "x is not defined"},
                    {"phase": "load", "message": "x is not defined"},
                ],
                "violations": [
                    {"directive": "img-src", "uri": "https://a.example/p.png"}
                ],
            }
        )
        self.assertEqual(
            [problem.code for problem in found],
            ["page_error", "csp_violation", "blank_page", "dead_controls"],
        )


class RenderedAppTurnTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        environment = patch.dict(
            os.environ, {"ATLAS_DATA_DIR": self.directory.name, "ATLAS_ARTIFACTS": "1"}
        )
        environment.start()
        self.addCleanup(environment.stop)

    async def turn(
        self, outcomes: list[object], reports: list[RenderReport], lang: str = "fr"
    ) -> tuple[Interaction, FakeModel, AsyncMock]:
        request = ChatRequest.model_validate(
            {
                "messages": [{"role": "user", "content": "Crée un minuteur"}],
                "lang": lang,
            }
        )
        request.__dict__["artifact_owner"] = OWNER
        model = FakeModel(outcomes)
        check = AsyncMock(side_effect=reports)
        item = Interaction()

        async def sink(event: dict[str, object]) -> None:
            return None

        token = sink_context.set(sink)
        try:
            with (
                patch(
                    "services.orchestrator.app_turn.GatewayModel.connect",
                    AsyncMock(return_value=model),
                ),
                patch("services.orchestrator.app_turn.app_render.check", check),
            ):
                await process_app_turn(
                    request, item, intent="create", kind="app", current=None
                )
        finally:
            sink_context.reset(token)
        return item, model, check

    async def test_a_broken_render_gets_one_repair_with_what_was_seen(self) -> None:
        broken = RenderReport(
            "problems",
            [RenderProblem("page_error", "Cannot read properties of null")],
        )
        item, model, check = await self.turn(
            [answer(THROWS), answer(APP)], [broken, RenderReport("ok")]
        )
        self.assertEqual(len(model.calls), 2)
        repair = str(model.calls[1][-1]["content"])
        self.assertIn("Atlas opened this file in the preview", repair)
        self.assertIn("Cannot read properties of null", repair)
        self.assertEqual(check.await_count, 2)
        artifact = item.artifacts[0]
        stored = artifact_store.read(
            str(artifact["id"]), OWNER, int(artifact["version"])
        ).content.decode()
        self.assertEqual(stored, APP)
        self.assertIn("render_check", item.latence_ms)

    async def test_two_broken_renders_end_with_an_honest_answer(self) -> None:
        blank = RenderReport("problems", [RenderProblem("blank_page")])
        item, model, _ = await self.turn([answer(BLANK), answer(BLANK)], [blank, blank])
        self.assertEqual(item.artifacts, [])
        self.assertIn("artifact_render", item.erreurs)
        self.assertEqual(
            item.reponse,
            app_render_failure("fr", ["blank_page"], unchanged_version=None),
        )
        self.assertIn("la page restait vide", item.reponse)
        self.assertNotIn("```", item.reponse)

    async def test_a_network_violation_asks_to_drop_the_files(self) -> None:
        blocked = RenderReport(
            "problems",
            [
                RenderProblem(
                    "csp_violation", "connect-src␟https://samples.example/k.wav"
                )
            ],
        )
        _, model, _ = await self.turn(
            # A file the static checks pass whose request the policy blocks.
            [answer(APP), answer(APP)],
            [blocked, RenderReport("ok")],
        )
        repair = str(model.calls[1][-1]["content"])
        self.assertIn("cannot load anything from the network", repair)
        self.assertIn("connect-src", repair)

    async def test_an_unavailable_browser_keeps_the_static_checks(self) -> None:
        item, model, _ = await self.turn(
            [answer(APP)], [RenderReport("unavailable", reason="runtime")]
        )
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(len(item.artifacts), 1)
        self.assertEqual(item.erreurs, [])


class FailureTextTests(unittest.TestCase):
    def test_every_language_names_what_was_seen(self) -> None:
        for language in ("fr", "en", "de", "es", "it"):
            text = app_render_failure(
                language, ["csp_violation", "page_error"], unchanged_version=3
            )
            self.assertIn("3", text)
            self.assertNotIn("{", text)
            self.assertNotIn("csp", text.lower())
        self.assertIn(
            "files from the internet", app_render_failure("en", ["csp_violation"])
        )


if __name__ == "__main__":
    unittest.main()
