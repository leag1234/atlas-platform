# Live quality judge (staging only)

Development tooling, decided by the owner on 2026-11-23. Every finished assistant answer
on the **staging** instance (every account) is scored 0 to 10 by the frontier judge
model; the score shows under the answer in the staging interface and every score is kept
in a private history. Nothing in `services/` calls a judge: the sidecar runs beside the
instance and only reads what staging already stores.

**Production never does this.** `guard.check()` refuses to start unless the instance
opted in with `ATLAS_LIVE_QUALITY_JUDGE=1` (set only in `infra/instances/staging.env`)
and its port base, data directory, interaction logs and public address all differ from
`infra/instances/production.env`; it also refuses everywhere if the flag ever appears in
`production.env` or `common.env` (a test checks both files). The interface shows nothing
unless the page carries the `atlas-live-quality` marker (same flag), and the endpoint
exists only in the staging nginx vhost (`nginx-staging.conf`).

Privacy: only conversations at the **Standard** level are sent to the judge. Atlas
Privacy (enhanced) and Strict conversations are never sent; they are recorded as
`skipped_privacy` and the interface says so. Requests use `store: false`.

## How a turn is captured

Open WebUI 0.13 keeps one row per message in `chat_message` (id `<chat>-<message>`, the
message id being the browser's `#message-<id>`), with parent link, content, files and
`done`. The sidecar polls that table read-only (SQLite `mode=ro`) every 10 s for
assistant rows with `done = 1`, walks the parent chain for the conversation, and reads
the files the request attached (Open WebUI `file` table and uploads). Links in the
answer are resolved to what staging stores: apps (`artifacts/blobs`), pictures
(`images/`, picture sets), project deliverables (`project-data/.../blobs`) and Open WebUI
files. Office files and PDFs are rendered with LibreOffice and poppler (text plus the
first three pages as PNG), as the offline judge does. The Atlas turn log has no chat or
message id; it is matched on time and output tokens for metadata only (task type).

## Judge

`gpt-6.2-sol`, standard (non-batch) Responses call, strict JSON schema:
`{score, verdict, defect_classes, worst_issue, language}`. Rubric: 10 is what the best
frontier assistant would answer to this request in this conversation; about one point
per minor defect, two to four per major one, 0 to 3 for a failed request, invented facts
that matter or a claim without the deed. The defect classes are C1 to C15 of
`frontier_judge/rubrics.json` and C16 to C19 of the class battery. C19 (slow answer) is
added by code when the answer took more than 240 s. An empty answer scores 0 (C5)
without a call. Scores below 5 get a second opinion from `gpt-6.2-astra` while the day's
spend is under 60 % of the daily cap; the arbiter's grade is then the one shown (the
first one is kept).

## Spend

Every call is reserved and settled in the offline judge's ledger
(`/opt/atlas-src/eval/frontier-judge/ledger.jsonl`), so the monthly cap (150 EUR,
`frontier_judge/models.json`) is shared. The daily cap is the ledger run cap of the run
`live-judge-<UTC day>`: `ATLAS_LIVE_JUDGE_DAILY_EUR`, default 5 EUR. Once it is reached
the answer is stored as `budget` and the interface shows "rating paused (budget)".
`backfill` uses its own run and its own cap (default 2 EUR).

## Store and history

`<ATLAS_DATA_DIR>/quality/scores.sqlite` (directory 700, file 600; never in git): message
and chat ids, a salted user hash, staging version (`git describe` of the checkout), times,
state, score, verdict, classes, worst issue, models, cost. Summaries for the curve:
`quality/summary/by-day/<day>.json` and `quality/summary/by-version/<version>.json`
(mean, median, distribution, share below 5, by class, by task type, cost), rewritten
every 10 minutes.

## Usage (on the build VM, from the staging checkout)

```
set -a; . /opt/atlas-src/staging.env; set +a
PY=/opt/atlas-src/staging/.venv/bin/python
$PY -m tools.eval.live_judge report --worst 20 --since 2026-11-23 [--version TAG] [--class C3]
$PY -m tools.eval.live_judge estimate --since 1d
$PY -m tools.eval.live_judge backfill --since 2026-11-23 --cap 2 [--dry-run]
$PY -m tools.eval.live_judge summary [--day 2026-11-23]
```

The service: `atlas-live-judge-staging.service` (install steps in its header), nginx:
`nginx-staging.conf`. Endpoint: `GET /api/atlas/quality/scores?chat=<id>&ids=<id>,...`;
the Open WebUI session cookie is verified by Open WebUI and only the person's own chats
are answered.

## Tests

`tests/test_live_judge.py`: fictional data, a temporary Open WebUI-shaped database and a
local fake HTTP server; never the real API.
