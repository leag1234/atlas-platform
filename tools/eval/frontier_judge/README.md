# Frontier judge (offline evaluation)

Development tooling only. A frontier OpenAI model grades Atlas outputs recorded by the
staging battery, so that people spend their time on the cases that matter. Nothing in
`services/` imports this code and the product never calls it: the CI sovereignty check
(`.github/workflows/ci.yml`, "No OpenAI/Anthropic as a runtime dependency") greps
`services/` only, and this directory uses no SDK (plain `urllib` against the HTTP API).

## What the judge may see

- Synthetic scenarios, the public corpus and staging battery transcripts from test accounts.
- Never real user conversations, never `/opt/atlas-src/private/`, never `BRAIN/`:
  `adapters.guard()` refuses those paths before anything is read.
- Requests are sent with `store: false`.

## Design

- **One binary rubric per defect class** (`rubrics.json`, classes C1 to C15 of the
  defect class draft of 2026-11-21): invariant, question, fictional pass and fail
  examples, required evidence.
- **Evidence first.** Each item carries typed evidence (request, answer, previous and
  current version, rendered pages as PNG through the image input, stored app HTML,
  side-view screenshots, render-check results, source text, privacy record...). When a
  class's required evidence is missing the verdict is `unknown` and no request is sent.
  Answers that hit the battery logger's length cap are marked as excerpts, so "cut
  answer" (C5) is not judged on them.
- **Structured output**: JSON schema with verdict `pass`, `fail` or `unknown`, verbatim
  quotes and confidence. Every quote is checked against the evidence text; quotes not
  found are reported (a judge error signal) and sent to the arbiter.
- **Pairwise comparisons in both orders** (same request, two builds, for example the
  trip battery of two releases): A/B then B/A, with an order-consistency column.
- **Models and cost** (`models.json`, prices of 2026-11-21): `gpt-6.2-sol` judges in
  bulk through the Batch API; `gpt-6.2-astra` re-judges only disagreements (with a
  code check, between the two pairwise orders, low confidence, unverified quotes) and a
  deterministic 10 % sample used to measure the agreement between the two judges. The
  served model id is recorded with every verdict.
- **Hard spend caps.** The ledger (`/opt/atlas-src/eval/frontier-judge/ledger.jsonl`)
  reserves an upper-bound estimate before anything is sent and settles it with the
  `usage` fields of the response. Open reservations count as spent. Defaults: 150 EUR per
  calendar month (UTC) for all runs together, 10 EUR per run; USD is counted as EUR
  one-for-one, which overstates the euro cost while the dollar is worth less.
- **Advisory only.** Until the agreement with the owner's labels is measured, the report
  says so and nothing gates on it.

## Usage (on the build VM, from the repository root)

```
set -a; . /opt/atlas-src/secrets.env; set +a      # OPENAI_API_KEY, never printed
L=/opt/atlas-src/logs/staging-battery
python3 -m tools.eval.frontier_judge run --battery $L/<run>.log \
    --trip /opt/atlas-src/scratchpad/trip_145.out --refetch-sources [--dry-run]
python3 -m tools.eval.frontier_judge calibration --battery $L/<run>.log ...   # owner review file
python3 -m tools.eval.frontier_judge agreement --review <review.md> --results <run>/results.json
python3 -m tools.eval.frontier_judge ledger
```

Outputs go to `/opt/atlas-src/eval/frontier-judge/runs/<run-id>/` (`report.md`,
`results.json`), outside the repository. The report lists per-class pass, fail and
unknown counts, the disagreements with the battery's code checks, the failing items with
quoted evidence, pairwise results, bulk versus arbiter agreement, the cost actually spent
per model, the month to date and the projected monthly cost at the current battery
cadence.

`--refetch-sources` fetches the cited public URLs again at judge time, because the
battery logs the URLs of a web turn but not the fetched text; such evidence is labelled
as re-fetched.

The battery scripts are never imported (importing `battery_multiturn.py` runs the
battery). Their JSON output and logs are read, and the few constants needed (questions,
the input notes) are read with `ast.literal_eval`.

## Tests

`tests/test_frontier_judge.py` runs against a local fake HTTP server, never the real API.
