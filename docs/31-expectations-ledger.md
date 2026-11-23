# 31. Expectations ledger

Status: implemented behind `ATLAS_EXPECTATIONS` (on by default), code in
`services/orchestrator/expectations.py`.

## 1. Problem

Owner request (2026-11-21): Atlas cannot yet judge its own answer against the
request. A drum machine built over several turns lost, two exchanges later, a
feature an earlier turn had added; Atlas could have measured that and repaired
it.

The checks that existed each compare against one reference:

| Check | Compares with | Misses |
|---|---|---|
| `app_features.lost` (PR 598) | version n-1 of the app | a feature lost two versions ago, a feature never built, a feature whose markup is kept but no longer works |
| `app_render.check` (PR 582/583) | nothing (errors, blank page, dead controls) | anything the person asked for |
| `readback.changes` + `deliverable_check` REVISION (PR 591, 602) | the current version of the document | requirements of earlier turns that the current version already lacked |
| `deliverable_check.review` | the last message and three earlier ones, read by a model | requirements older than three messages; nothing executable |

Nothing holds the full list of what the person stated for a deliverable across
the whole conversation, and nothing checks every version against all of it.
That is defect class C1 (lost work across turns) when an older requirement
silently disappears, C2 (claim without the deed) when the answer says "done"
for a feature that does not work, and C14 (request misread) when a requirement
was never met in the first place (classes from the supervisor's defect class
draft).

## 2. Survey

Owner rule "survey before building" (2026-11-11). What exists, and what Atlas
takes from it:

- **Multi-turn degradation is measured and large.** Laban et al., *LLMs Get
  Lost In Multi-Turn Conversation* (arXiv 2505.06120, ICLR 2026): all tested
  open and closed models lose on average 39 % of their single-turn performance
  when the requirements arrive over several turns, mostly through
  unreliability; models anchor on early assumptions and do not recover. Their
  mitigation is to restate the full requirement set at once, which is what the
  ledger's brief does before each write.
- **Decomposing a request into yes/no requirements makes checking reliable.**
  InFoBench (Qin et al., arXiv 2401.03601, Findings of ACL 2024) defines the
  Decomposed Requirements Following Ratio: an instruction split into simple
  criteria, each judged on its own. TICK (Cook et al., arXiv 2410.03608) shows
  that LLM-generated checklists match human ones in quality and raise judge
  agreement with humans (46.4 % to 52.2 %), and that checklist-guided
  self-refinement improves outputs. The ledger is such a checklist, kept over
  the conversation instead of per instruction.
- **Executable tests beat reading for interactive artifacts.** WebGen-Bench
  (Lu et al., arXiv 2505.03733, NeurIPS 2025) writes, for each requested
  website functionality, a test case pairing an operation with an expected
  result and runs it with a UI agent. ArtifactsBench (Tencent Hunyuan, arXiv
  2507.04952) renders generated artifacts in a sandbox, captures screenshots
  across interactions and scores them against per-task checklists with a
  multimodal judge. Atlas takes the operation/expected-result pair for apps,
  run by code in the existing render sandbox (a fixed vocabulary, no agent),
  and keeps a model judge only for what no assertion fits.
- **Critique must be grounded in tool output.** CRITIC (Gou et al., 2024)
  shows that self-correction works when the critique comes from tools, not from
  the model's own belief; Self-Refine (Madaan et al., 2023) and Reflexion
  (Shinn et al., 2023) give the bounded feedback-repair loop. The repair here
  names each failed requirement with what code measured.
- **Spec-driven agent development.** GitHub Spec Kit (MIT licence, 2025)
  keeps a written specification and task list that agents implement against;
  BDD (Gherkin "Given/When/Then") and EARS (Mavin et al., 2009) structure
  requirements as testable statements. These are developer tools for whole
  projects, too heavy to run inside a chat turn, but they confirm the shape:
  one living spec, each item with an acceptance check.

Nothing found ships as a reusable library for a chat assistant's per-turn
ledger: the benchmarks are offline evaluations, the spec tools target
repositories. The ledger is therefore built here, small, on Atlas's existing
render sandbox, document readback and gateway.

## 3. Data model

One ledger per deliverable:

```
Ledger { kind: app | document | deck | spreadsheet, next_id, requirements[] }
Requirement {
  id          "R3"
  statement   short, in the person's language ("Tempo control")
  turn        the number of the person's message it came from
  status      active | changed (replaced_by: "R7") | removed
  method      code | render | judge
  check       the assertion (below)
  bound       app only: the element id a passing check found, tried first next time
  last        passed | failed | unchecked, for the delivered version
}
```

Checks, a fixed vocabulary that code validates (an invalid check becomes
`judge`):

- app `code`: `role` (button, slider, input, select, drawing, any), `words`
  (label, text, id, title or aria-label), `action` (none, click, set, key with
  an allowed key), `expect` (present, changes, stores, sound; passes when one
  holds). Run by `app_render_worker.run_assertion` in the same sandboxed
  browser as the render check; every interaction runs on a page of its own,
  so a sequencer started by one check cannot make another pass.
- app `render`: `palette` (the main colours by painted area differ from those
  of the version the request started from, measured by code on both) or
  `visible` (the element is shown on a 480 x 720 page).
- document, deck, workbook `code`: `part` (title, section, slide, table,
  picture, chart, text), `words`, optional `count` (slides, sections, rows),
  checked on the `Content` that `publish_document` builds the file from.
- document, deck, workbook `code` with `part: size`: a size or count of the
  whole file, `unit` (slides, pages, words, items, rows, sections), `count`
  and `relation` (exact, about, max, min). Measured by code on the built file:
  slides of the PPTX, pages of the PDF that the bounded preview renderer
  (LibreOffice, no network) makes of a DOCX or PPTX, words of the drawn text,
  bullets, table rows, sections. "About N" passes within 20 % of N, at least
  1 either way (about one page: 0 to 2 pages; about 300 words: 240 to 360);
  pages and words asked "exactly" read as about. Everything in the file
  counts: an unrequested section (a Sources page) that takes it over the size
  fails the requirement, and the repair lists the sections. Found on
  staging: a deck asked with 5 slides came with 7, reports
  asked at about one page came at 6 or 7 pages with a Sources page nobody
  asked for.
- `judge`: the statement and the file go to one JSON call.

## 4. Where it lives

`ATLAS_DATA_DIR/expectations/ledgers.sqlite`, table `ledgers(key, version,
updated_at, body)`, 30-day retention like the artifact store.

- App: key `app:<artifact id>`, version = the artifact version. Each stored
  version has the ledger it was checked against. A turn that stores no version
  saves what it extracted under the current version, so a requirement stated
  in a failed turn is not lost.
- Document: key `file:` + a hash of the conversation scope and the file name
  the file was saved as; versions count up per publication. A change of a
  delivered file loads the ledger under that file's name.

## 5. The loop

Per app turn (`app_turn.process_app_turn`):

1. Load the ledger of the current version (empty for a new app).
2. Extraction, in parallel with the media downloads and the render of the
   current version: the turn's model reads the latest message against the
   list and returns `{"new", "changed", "removed"}` as JSON. When the list is
   empty and the conversation has earlier messages, they are read too (an
   app created before the ledger existed is seeded from its history).
3. Code applies the update. A change or removal needs a `quote`: the person's
   words, found by code in what they wrote (whole quote, or 80 % of its words
   for a copied typo). A requirement not mentioned stays active. Duplicates
   are dropped; at most 40 active requirements.
4. The writer receives the brief: every active requirement with how it is
   checked, and what is no longer required.
5. After the file passes validation and the render check, every active
   requirement is checked (the app assertions ran in that same render check).
   Executable checks that could not run (render check off or unavailable)
   go to the judge.
   An element missing or hidden when the page opens may belong to a later
   state (a replay button at the end of a game, a panel a press opens, a
   message after a countdown). On a page of its own, the check then plays the
   app forward: each step presses the next visible control in turn (never
   the element itself) and runs the page clock (1.5 s after a press, 5 s when
   nothing can be pressed), at most 60 steps and 2.5 s of real time within
   the assertion budget. Reached, the element is checked as usual. Not
   reached, the requirement is unchecked and goes to the judge, which reads
   the file with what the browser saw; without a verdict it stays unchecked.
   A requirement fails only on positive evidence: the element was shown and
   its interaction did nothing, or it is absent from the page, from every
   state played, and from the text the file can show (strings, attribute
   values, text between tags; a script identifier such as `const bpm` does
   not count). Staging -151: a quiz's "Replay" button, hidden until the last
   question, was reported as not working in three versions that had it,
   while the answer said it worked and the plan card counted 5/6.
6. Unmet requirements go back to the writer by id, statement, message number,
   check and measured reason; it repairs from the stored version. At most
   `MAX_REPAIRS` (2) repairs for unmet requirements, within the turn's
   deadline (each call still needs `MINIMUM_CALL_SECONDS`).
7. The best file is delivered (fewest unmet requirements, losses and soft
   defects); the ledger is settled (statuses, pinned element ids) and stored
   with the new version.

Per document publication (`project_document_tools.publish`): the same
extraction once per turn, code checks on the content, the judge for the rest;
an unmet requirement refuses the publication with the same named list (at most
twice in a turn, counting as the deliverable check's own refusal), after which
the file is published and the unmet requirements are gaps that the answer must
state (deliverable_check NOTE).

## 6. Honest answer and plan card

- A delivered app with unmet requirements carries, in the person's language
  (en, fr, de, es, it), a sentence naming them, for example "Not working yet
  in this version, against what you asked: "Tempo control".", and the
  requirements that could not be checked: "Not checked automatically: ...".
  The answer never claims them.
- The plan card's check step shows the measured count, for example
  "Checking the app (5/5 requirements met)", and the unchecked ones when
  there are any ("5/6 requirements met, 1 not checked"), so that the card,
  the note and the stored ledger say the same thing
  (`ChatPlan.step_note`).

## 7. Bounds and cost

- Code and render checks are free (they run in the render check's browser,
  `ASSERT_BUDGET_MS` = 6 s more at most; interactions not run in time go to
  the judge).
- Model calls per turn: one extraction (at most 900 output tokens, 30 s) and
  at most `MAX_JUDGE_CALLS` = 3 judge calls (one per checked version, only
  when a requirement has no executable check), each capped at 0.02 EUR,
  reasoning off, temperature 0, through the gateway (open models only; strict
  privacy is passed on). Two profiles are tried within that time: the judge
  asks the turn's own profile first (60 % of the time), then the fast check
  profile (`deliverable_plan` role in routing.yaml); the extraction, a short
  structured reading, asks the fast profile first, as deliverable_check
  orders its plan since staging -91. Measured on staging (2026-11-21, live
  drum machine in French and English): 6 of 8 `atlas-mistral` extraction
  calls ended in a 504 at 18 s or a 502, which added that wait to every app
  turn, and the fast profile then answered in a few seconds. Their cost is added to the turn's spending.
- Every turn logs one `expectations` JSON line: active, passed, failed,
  unchecked, extraction and judge call counts, repairs, cost, ledger changes.

## 8. Failure modes

| Failure | Effect | Handling |
|---|---|---|
| Extraction call fails or returns no JSON | no new requirement this turn | the ledger is kept as it was; logged `extraction_failed` |
| The model invents a removal | a requirement would disappear | refused unless the quote is in the person's words |
| The model states a requirement too narrowly (wrong words for the control) | a false failure | the brief tells the writer the check, so it labels the control; the repair names the check; after two repairs the answer says it plainly instead of looping |
| A control is renamed | words no longer match | the pinned element id is tried first |
| A requirement is met only invisibly (tempo changes without any visible value) | `changes` fails | the brief states the check before writing; the judge covers what no assertion fits |
| Render check unavailable | no executable check | the judge reads the file (bounded) |
| Judge call fails | requirement unchecked | said as "not checked", never claimed |
| A feature only shows in a later state (end of a game) | hidden or absent as the page opens | the app is played forward to reach it; not reached, it is unchecked and judged, never failed |
| Old app created before the ledger | empty ledger | seeded from the earlier messages on the first follow-up |

## 9. Relation to the defect classes

- C1 (lost work across turns): the ledger is cumulative; a requirement of
  message 2 is checked at message 5 even when version 4 already lacked it.
  `app_features.lost` and the document revision diff stay: they catch losses
  of things nobody stated as a requirement.
- C2 (claim without the deed): the answer only stands for requirements that
  passed; unmet and unchecked ones are named. The claims-match-deeds verifier
  (another branch) checks the prose; this checks the artifact.
- C14 (request misread): a requirement never met is a failure from its first
  check, independently of earlier versions.
