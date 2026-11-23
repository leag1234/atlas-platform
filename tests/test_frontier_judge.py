"""Offline frontier judge: fake HTTP server only, never the real API."""

from __future__ import annotations

import datetime as dt
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar

from tools.eval.frontier_judge import adapters, judge, report
from tools.eval.frontier_judge.client import OpenAIHttp
from tools.eval.frontier_judge.ledger import BudgetExceeded, Ledger, Price


def _response(verdict: str, quote: str) -> dict[str, Any]:
    payload = {
        "verdict": verdict,
        "evidence": [{"source": "answer", "quote": quote}],
        "reason": "r",
        "confidence": 0.9,
    }
    return {
        "id": "resp_1",
        "model": "gpt-6.2-sol-2026-11-01",
        "status": "completed",
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": json.dumps(payload)}],
            }
        ],
        "usage": {"input_tokens": 1000, "output_tokens": 200},
    }


class FakeOpenAI(BaseHTTPRequestHandler):
    requests: ClassVar[list[tuple[str, str, bytes]]] = []
    files: ClassVar[dict[str, bytes]] = {}

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - signature of the base class
        return

    def _send(self, obj: Any, raw: bytes | None = None) -> None:
        body = raw if raw is not None else json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        FakeOpenAI.requests.append(("GET", self.path, b""))
        if self.path.startswith("/v1/batches/"):
            self._send(
                {"id": "batch_1", "status": "completed", "output_file_id": "file_out"}
            )
        elif self.path == "/v1/files/file_out/content":
            lines = []
            for line in FakeOpenAI.files["file_in"].decode().splitlines():
                row = json.loads(line)
                body = _response("fail", "Version 2 is ready")
                lines.append(
                    json.dumps(
                        {
                            "custom_id": row["custom_id"],
                            "response": {"status_code": 200, "body": body},
                        }
                    )
                )
            self._send(None, ("\n".join(lines) + "\n").encode())

    def do_POST(self) -> None:
        data = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        FakeOpenAI.requests.append(("POST", self.path, data))
        assert self.headers["Authorization"] == "Bearer test-key"
        if self.path == "/v1/responses":
            self._send(_response("pass", "Version 2 is ready"))
        elif self.path == "/v1/files":
            FakeOpenAI.files["file_in"] = data.split(b"\r\n\r\n", 2)[2].rsplit(
                b"\r\n--", 1
            )[0]
            self._send({"id": "file_in"})
        elif self.path == "/v1/batches":
            self._send({"id": "batch_1", "status": "validating"})


def _item() -> adapters.Item:
    it = adapters.Item("run/app/timer", "run", "app_turn", ["C1", "C2", "C15"])
    it.add("request", "requests", "add a 10 second timer")
    it.add("answer", "answer", "Version 2 is ready with the timer.")
    it.add("previous_version", "v1", "<button>Replay</button>")
    it.add("current_version", "v2", "<div id=timer></div>")
    it.code_checks["C1"] = [("multiturn.app.timer.features_kept", "PASS", "")]
    return it


class FrontierJudgeTests(unittest.TestCase):
    server: ClassVar[ThreadingHTTPServer]

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeOpenAI)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self) -> None:
        FakeOpenAI.requests.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        port = self.server.server_address[1]
        self.http = OpenAIHttp(
            api_key="test-key", base_url=f"http://127.0.0.1:{port}/v1", retries=0
        )
        self.models = judge.load_json("models.json")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _ledger(self, run_cap: float = 10.0, month_cap: float = 150.0) -> Ledger:
        return Ledger(self.dir / "ledger.jsonl", "run-1", month_cap, run_cap, 1.0)

    def test_missing_evidence_is_unknown_without_a_call(self) -> None:
        rubs = judge.rubrics()
        it = _item()
        self.assertEqual(judge.missing_evidence(it, rubs["C1"]), [])
        self.assertEqual(judge.missing_evidence(it, rubs["C15"]), [["model_record"]])
        self.assertEqual(len(rubs), 15)
        for rubric in rubs.values():
            self.assertTrue(
                rubric["pass_examples"]
                and rubric["fail_examples"]
                and rubric["required_evidence"]
            )

    def test_standard_call_records_spend_and_served_model(self) -> None:
        it = _item()
        body = judge.verdict_body(it, judge.rubrics()["C1"], "gpt-6.2-sol", 500)
        self.assertEqual(body["text"]["format"]["type"], "json_schema")
        self.assertFalse(body["store"])
        task = judge.Task("run/app/timer::C1", it.item_id, "C1", body, source="run")
        ledger = self._ledger()
        runner = judge.Runner(self.http, ledger, self.models, self.dir, poll_seconds=0)
        results, notes = runner.run(
            [task], "standard", {task.task_id: judge.corpus_of(it)}, 10
        )
        self.assertEqual(notes, [])
        self.assertEqual(results[0].verdict, "pass")
        self.assertEqual(results[0].served_model, "gpt-6.2-sol-2026-11-01")
        self.assertEqual(results[0].unverified_quotes, [])
        self.assertAlmostEqual(results[0].usd, (1000 * 2.0 + 200 * 10.0) / 1e6)
        month, run = ledger.totals()
        self.assertAlmostEqual(month, results[0].usd)
        self.assertAlmostEqual(run, results[0].usd)

    def test_batch_round_trip_uses_batch_prices(self) -> None:
        it = _item()
        tasks = [
            judge.Task(
                f"run/app/timer::{c}",
                it.item_id,
                c,
                judge.verdict_body(it, judge.rubrics()[c], "gpt-6.2-sol", 500),
                source="run",
            )
            for c in ("C1", "C2")
        ]
        ledger = self._ledger()
        runner = judge.Runner(self.http, ledger, self.models, self.dir, poll_seconds=0)
        corpora = {t.task_id: judge.corpus_of(it) for t in tasks}
        results, _ = runner.run(tasks, "batch", corpora, 10)
        self.assertEqual(
            sorted(r.task_id for r in results), sorted(t.task_id for t in tasks)
        )
        self.assertTrue(all(r.verdict == "fail" and r.mode == "batch" for r in results))
        posted = [p for m, p, _ in FakeOpenAI.requests if m == "POST"]
        self.assertEqual(posted, ["/v1/files", "/v1/batches"])
        _, run = ledger.totals()
        self.assertAlmostEqual(run, 2 * (1000 * 1.0 + 200 * 5.0) / 1e6)

    def test_caps_stop_before_sending(self) -> None:
        ledger = self._ledger(run_cap=0.0001)
        it = _item()
        task = judge.Task(
            "t::C1",
            it.item_id,
            "C1",
            judge.verdict_body(it, judge.rubrics()["C1"], "gpt-6.2-astra", 3000),
        )
        runner = judge.Runner(self.http, ledger, self.models, self.dir, poll_seconds=0)
        results, notes = runner.run([task], "standard", {"t::C1": ""}, 10)
        self.assertEqual(results, [])
        self.assertIn("run cap", notes[0])
        self.assertEqual(FakeOpenAI.requests, [])

    def test_monthly_cap_counts_open_reservations_and_resets_by_month(self) -> None:
        now = [dt.datetime(2026, 11, 30, tzinfo=dt.UTC)]
        ledger = Ledger(self.dir / "l.jsonl", "a", 1.0, 5.0, 1.0, now=lambda: now[0])
        ledger.reserve("k1", 0.8)
        with self.assertRaises(BudgetExceeded):
            ledger.reserve("k2", 0.3)
        now[0] = dt.datetime(2026, 12, 1, tzinfo=dt.UTC)
        ledger.reserve("k3", 0.3)
        self.assertAlmostEqual(ledger.totals()[0], 0.3)
        self.assertAlmostEqual(Price(4.0, 20.0).usd(1_000_000, 100_000), 6.0)

    def test_quotes_are_verified_against_the_evidence(self) -> None:
        corpus = "Arrivée 11:50 pour 12:30 demandé. "
        ev = [
            {"source": "answer", "quote": "Arrivée 11:50  pour 12:30"},
            {"source": "answer", "quote": "Arrivée 11:40"},
        ]
        self.assertEqual(judge.verify_quotes(ev, corpus), ["Arrivée 11:40"])

    def test_private_paths_are_refused(self) -> None:
        with self.assertRaises(adapters.ForbiddenPath):
            adapters.guard("/opt/atlas-src/private/owner/x.json")
        with self.assertRaises(adapters.ForbiddenPath):
            adapters.guard("/opt/atlas-src/repo/BRAIN/interactions/a.jsonl")

    def test_trip_adapter_and_pairwise_orders(self) -> None:
        script = self.dir / "trip_script.py"
        script.write_text(
            'CASES = [("a-b", "be at B by 9", True)]\nLOCALE = {}\nTZ = {"a-b": "Europe/Paris"}\nimport this_module_is_never_imported\n'
        )
        outs = []
        for n in ("trip_1.out", "trip_2.out"):
            p = self.dir / n
            p.write_text(
                "=== a-b grant=True dialog_clicked=True seconds=3 text_question=False\nOption 1: arrive 08:50 for 09:00 requested.\n"
            )
            outs.append(p)
        items = [i for p in outs for i in adapters.trip_items(p, script)]
        self.assertEqual(len(items), 2)
        self.assertIn("request", items[0].kinds())
        from tools.eval.frontier_judge import cli

        tasks, local, _ = cli.plan(items, {"C14"}, "gpt-6.2-sol", 500)
        pair_ids = sorted(t.task_id for t in tasks if t.pair)
        self.assertEqual(len(pair_ids), 2)
        self.assertTrue(pair_ids[0].endswith("::ab") and pair_ids[1].endswith("::ba"))

    def test_battery_log_sections_and_checks(self) -> None:
        log = (
            "== 8 deliverables\n"
            + json.dumps({"deck": {"question": "make 5 slides", "answer": "done"}})
            + "\nCHECK deliverable.deck PASS (7 slides)\nCHECK rawtext PASS (3)\n"
        )
        self.assertEqual(
            adapters.sections(log)["8"][0]["deck"]["question"], "make 5 slides"
        )
        self.assertEqual(
            adapters.parse_checks(log)["deliverable.deck"], ("PASS", "(7 slides)")
        )

    def test_review_file_and_agreement(self) -> None:
        it = _item()
        path = self.dir / "review.md"
        counts = report.write_review([it], judge.rubrics(), ["C1", "C2"], 30, path)
        self.assertEqual(counts, {"C1": 1, "C2": 1})
        text = path.read_text().replace(
            "- task: run/app/timer::C1\n", "- task: run/app/timer::C1\n", 1
        )
        text = text.replace("label:\n", "label: pass\n", 1)
        path.write_text(text)
        labels = report.read_labels(path)
        self.assertEqual(labels, {"run/app/timer::C1": "pass"})
        stats = report.agreement(labels, {"run/app/timer::C1": {"verdict": "pass"}})
        self.assertEqual(stats["C1"]["agreement"], 1.0)


if __name__ == "__main__":
    unittest.main()
