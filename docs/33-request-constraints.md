# 33. Request constraints: read, check, repair once

Status: first slice implemented, `services/orchestrator/constraints/`,
switched by `ATLAS_ANSWER_CONSTRAINTS` (`off`, `code` the default, `full`).

## 1. Problem

Atlas checks by code what code can measure: files, pages, pictures, versions,
figures, claims of a deed. What it did not check is how the request was
read. A person writes form instructions that are easy to state and easy to
miss: begin the reply with given words, one sentence and no introduction,
the answer alone, a status line under the file link, three bullets rather
than cards, a part on the slide and not in the speaker notes, titles in a
given order, a word not to use, propose first and create nothing yet. They
also restate small details across turns. Nothing in the turn listed these
constraints and verified each one on what was produced before it was shown.

The expectations ledger (docs/31) already keeps the cumulative requirements
of a deliverable (an app, a document, a deck) and checks them on the file.
This note adds the same discipline to the reply itself, and a structured view
of files that the same checkers read, so that one vocabulary of constraints
covers both.

## 2. Survey

Owner rule "survey before building". Sources read on 2026-11-24.

Verifiable instructions and decomposed requirements:

- IFEval (Zhou et al., arXiv 2311.07911): about 500 prompts built from 25
  instruction types that a program checks, no model in the loop. Its
  registry (Apache-2.0, google-research/instruction_following_eval, also in
  lm-evaluation-harness) is the model for Atlas's closed vocabulary.
  M-IFEval (Dussolle et al., arXiv 2502.04688, Findings of NAACL 2025)
  extends it to French, Japanese and Spanish and shows scores vary widely by
  language and instruction type: the phrase tables here are written per
  language, not translated from English.
- IFBench (Pyatkin et al., arXiv 2507.02833, NeurIPS 2025 D&B; Apache-2.0):
  models overfit to the IFEval constraint set and generalise poorly to 58
  new verifiable constraints. A fixed list of checkers will never be
  complete; the model reading and the verifier cover the rest.
- InFoBench (Qin et al., arXiv 2401.03601, Findings of ACL 2024): splitting an
  instruction into yes/no requirements (DRFR) is more reliable than one
  overall score. FollowBench (Jiang et al., arXiv 2310.20410, ACL 2024)
  grades content, situation, style, format and example constraints added one
  at a time; ComplexBench (Wen et al., arXiv 2407.03978, NeurIPS 2024 D&B)
  shows that compositions (and, chain, selection) are where models fail, and
  checks each constraint with rules first and a model second. CFBench (Zhang
  et al., arXiv 2408.01122, ACL 2025) adds priorities between requirements.
- Multi-turn: Multi-IF (He et al., arXiv 2410.15553, 8 languages) measures
  accuracy falling at each turn; MultiChallenge (arXiv 2501.17399, Findings
  of ACL 2025) names instruction retention and versioned editing among the
  hardest cases; Laban et al. (arXiv 2505.06120, ICLR 2026) measure a 39 %
  average loss when requirements arrive over several turns. Hence one
  extraction over the conversation, the previous reply included (a person
  writes "the second outline").

Checklists, decomposition and repair:

- TICK (Cook et al., arXiv 2410.03608): generated yes/no checklists raise the
  agreement of a model grader with people and support self-refinement.
  RocketEval (Wei et al., arXiv 2503.05142, ICLR 2025; MIT) shows small models
  grade reliably with a per-instance checklist, their weakness being
  uncertainty and position bias. RLCF (Viswanathan et al., arXiv 2507.18624)
  combines model graders with verifier programs per checklist item.
- DeCRIM (Ferraz et al., arXiv 2410.06458, Findings of EMNLP 2024):
  decompose, critique, refine improves an open model (Mistral) on real user
  requests with several constraints; the critic's quality decides the gain.
  AutoIF (Dong et al., arXiv 2406.13542, ICLR 2025; Apache-2.0) writes a
  verification function per instruction and keeps only what executes.
- Self-correction without reliable feedback does not work (Huang et al.,
  arXiv 2310.01798, ICLR 2024; Kamoi et al., TACL 2024, arXiv 2406.01297);
  it works with external feedback such as code execution. Hence: code checks
  first, the model only for what code cannot read, and its verdicts are
  themselves checked by code.

Verifier failure modes:

- False passes: graders are lenient, and miss the rarer partial and failed
  cases at constraint level (MCJudgeBench, arXiv 2605.03858; RubricEval,
  arXiv 2603.25133). A model grader favours its own text (Panickssery et
  al., arXiv 2404.13076). Hence the evidence rule below, and the fast check
  profile (another model when routing provides one) for reading and
  verifying.
- Over-strict rejection and loops: a wrong failure sends a right reply to
  repair, and a repair can break what passed. Hence one repair, kept only
  when it meets strictly more constraints and breaks none.
- Small open verifiers: Prometheus 2 (arXiv 2405.01535; Apache-2.0 weights)
  and MiniCheck (arXiv 2404.10774; MIT models) are open evaluator models that
  could run on owned hardware; not needed for this slice, noted for strict
  privacy (section 9).

Libraries: the IFEval checkers are English-centric and score a whole
response; Guardrails AI (Apache-2.0) validates structured output. Neither
reads constraints from a multilingual conversation nor checks a PPTX layout,
so the pipeline is built here, small, on Atlas's own document readers.

## 3. Constraint model

A constraint is atomic and typed (`model.py`):

| kind | parameters | checked by |
|---|---|---|
| starts_with, ends_with | exact text | code |
| contains, excludes | exact text, optional region / page | code |
| sentences, paragraphs | count, relation (exact, min, max) | code |
| items | count, relation | code (every list item) |
| list_style | bullets, numbered, checkboxes, plain | code |
| order | texts in order | code |
| titles | page or slide titles in order (files) | code |
| after_file | line beginning with text, under the file link | code |
| no_intro | no lead-in sentence | code |
| only | the requested output and nothing around it | code |
| no_new_file | nothing made or changed in this turn | code (turn facts) |
| judge | a statement | verifier model |

Each carries `scope` (answer or file), `quote` (the person's words), the turn
it was stated in, and `lasting` (applies to later replies). Code validates
every constraint, whoever proposed it:

- the quote must be in the person's messages (whole, or 80 % of its words);
- an exact text or list of texts must have been written by the person, or
  shown in the previous reply they refer to ("I take the second outline");
- a forbidden word must be quoted or named as a word ("without the word X"),
  never a kind of content ("no explanations");
- "lasting" needs the person to say so ("always", "from now on", and the
  same words in the other four languages); otherwise a constraint on a
  reply binds that reply only, never a later one or a file made later;
- the start of a part ("start the second paragraph with ...") is a verifier
  item, not a check of the reply's first words; titles of a reply (it has
  no pages) are checked as an order; a count of list items next to "no
  list" counts something else and goes to the verifier.

## 4. Pipeline (one pass per turn, never a loop)

1. Code reads the latest message (`phrases.py`): quoted text after "begin
   with / commence par / beginne mit / empieza con / inizia con", "end
   with", "under the file", "without the word", "one sentence / at most N
   sentences", "no intro", "only X" (only_request), "no need to redo the
   file / before changing anything" and the deferral phrases
   (packages/deferred_intent). A form constraint next to a file request ("a
   case in one sentence on each slide") belongs to the file and is left to
   the model reading unless a verb of telling governs it.
2. `full` only, and only when the message has quotation marks or form words:
   one model reading returns typed constraints as JSON (900 tokens at most,
   12 s, fast check profile first), validated as in section 3.
3. The reply becomes a structured view (`views.py`): blocks with a kind
   (heading, paragraph, bullet, numbered, checkbox, quote, code, file link,
   picture), without the plan card and the step details. Files become the
   same view: PPTX (titles, body paragraphs with their real bullet
   properties, speaker notes as a region), DOCX (styles, list numbering,
   checkbox glyphs, headers and footers, tables), PDF (pdftotext per page,
   first line as title, last set-apart line as footer, bullet and checkbox
   glyphs), XLSX (sheets and cells), HTML (headings, list items, buttons and
   inputs, header and footer), SVG (text elements, top, middle, bottom).
4. Code checks every constraint it can (`checks.py`); comparisons ignore
   case, spacing, quotation style and markdown, and trailing punctuation for
   a beginning or an end. Undecidable means unchecked, never failed.
5. `full` only: one verifier call for the `judge` items. A verdict stands
   only with an evidence quotation that code finds in the reply; without
   one, the item is unchecked (neither claimed nor reported).
6. Repairs code makes alone: the requested output alone
   (only_request.restrict), the lead-in dropped, the requested line moved
   under the file link.
7. `full` only: one model rewrite listing each failed constraint with what
   was found. Code refuses a rewrite that drops or adds a link, drops a code
   block or adds a figure (list numbers aside). The rewrite is kept only if
   it meets strictly more constraints and breaks none that passed.
8. What code decided is still not met is said in one closing sentence in
   the person's language, impersonal (right in either register), naming what
   is missing, for example "This reply does not yet meet the request:
   sentences: 5, asked: at most 4." A reply already streamed only gets that
   sentence.

Holding: a reply is held (not streamed) while it is checked only when code
found a constraint on it (`code`), or when the message has quotation marks
or form words (`full`). The existing holder of checked answers does this
(`chat_pipeline._HeldAnswer`); the reply is then sent whole.

## 4 bis. Never worse (staging -197)

The class suite on staging with `full` (-197 against -196) found the pass
making replies worse. Causes and rules, all enforced by code:

- A turn whose Work run stopped on provider refusals (HTTP 429) got code's
  explanation as its reply; the pipeline checked that explanation against
  the request, and the rewrite, which saw only the reply and the
  constraints, replaced it with an invented answer off the subject, a copy
  of the person's instruction as a line, and a false gap. Rule: a reply
  code wrote for a stopped turn (its plan finished without success) is
  neither checked nor rewritten.
- Counts that describe one part of the reply ("one menu of four dishes
  and one of six", "a closing sentence for the card", "three points in
  each section") were checked on the whole reply. Rule: such constraints
  are part-scoped (`Constraint.part`, `model.part_of`), as are exact counts
  of one kind that cannot all hold on the whole reply. They are checked
  per part when the reply's lists are delimited (each run of list items is
  a part; "one" constraints are matched one to one with the parts, only
  when their numbers are equal), otherwise undecided. The same rule sends
  clashing layout counts of a file to the judge
  (`expectations.layout_part_scoped`).
- The rewrite gets the same conversation as the answer. It replaces the
  reply only when code accepts it (`pipeline.refusal`: not empty, every
  link, picture, file and code block kept, no content words absent from
  the reply, the conversation and the asked texts beyond 20 %, no line
  copied from the person's last message) and it passes strictly more
  constraints, losing none that passed (`pipeline.better`). Code's own
  repairs pass the same gate.
- A gap is said only for a failure code decided; an undecided constraint
  or a verifier's verdict never is.
- An empty result never replaces the reply.
- One `answer_constraints` line per turn (warning level, so it reaches the
  journal), counts only: constraints by kind, part-scoped, passed, failed,
  failed for certain, undecided, repairs attempted, kept, dropped and why,
  calls, cost, seconds, or the reason the turn was skipped.

Measured offline on the 16 turns of -197 where the pass ran (replies
without their notes, earlier turns as history, `code` mode): gap notes 2
to 0 (one false, a proposed sentence read as the reply's length; one true,
a line written inside the paragraph above the link, now moved under it by
code). The outlines turn with the readings its note shows: false gap on 3
items to none; its stopped-turn explanation is left as written. The
labelled cases of section 6 are unchanged (reading 43/44 and 43/43; 18/18
and 17/17 violations, no false one). The empty reply of -197 (a request to
resend a link) was not the pipeline: the chat model searched the web, every
engine refused (upstream_blocked) and the turn ended on search_unavailable,
an error event with no text.

## 5. What this slice covers

In the product path (`chat_pipeline._answer_constraints`, after the answer
checks, before number provenance): every turn except pictures (their bare
answer is pictures/bare.py), any task (chat, Work, apps), reply scope only.
The "made in this turn" fact is a file, an app or picture version, a drawn
image, or a link to a deliverable in the reply.

## 5 bis. File layout in the deliverable checks

The expectations ledger (docs/31) has a code check `part: "layout"` for
documents, decks and workbooks: `rule` (titles, list_style, items, contains,
excludes, order, starts_with, paragraphs), `texts`, `style`, `count`,
`relation` and `where` (each, slide:N or page:N, footer, header, notes).
The extraction is told when to use it; code keeps it only when its exact
texts are the person's words, otherwise it goes to the judge.

`document_check` builds the structured view of the built file (section 4,
step 3) once per publication, only when a layout requirement is active, and
`check_document` decides each layout requirement on it. A verdict code
cannot give (boxes to tick in a PDF, where they are drawn rather than
written; a file it cannot read) goes to the judge as before. A failure
names what was measured ("page 2: no list; points drawn as separate
cards"), and goes to the existing refusal and repair (at most twice a turn,
then said by name).

The views read Atlas's own renderer as it writes:

- themed decks by the role in each shape's name (title, deck title, list,
  point, footer, slide number), as readback.py does: a card or a column is
  a "card" block, not a list item; speaker notes are their own region, so a
  text asked on the slide is not met by the notes;
- DOCX by style (Title, Heading, List Bullet, List Number), box glyphs for
  points to tick, section headers and footers;
- PDF by the lines readback.py reads (size, place): the running footer under
  the bottom margin, headings by size, list items by their indent, wrapped
  lines joined into one block.

Titles mean the slide or sheet titles for decks and workbooks, the section
headings in order (the document's own title aside) for documents.

A deck draws two to four developed points as cards, and a short first slide
as the cover. When the person asked for bullet points, that layout cannot
pass. `Section.bulleted` (and the same field of a slide in the deck outline)
draws the slide as a bulleted list; code sets it before rendering whenever
the turn's ledger, or the one stored with the file, holds a list_style
bullets requirement (`expectations.bulleted_as_asked`), and the repair line
tells the writer the same.

only_request: German words written with "ae" in its phrase tables now also
match their folded form ("Erklärung" folds to "erklarung").

## 6. Measurements (2026-11-24)

Offline, hand-labelled fictional cases (`tests/fixtures/request_constraints.json`,
`tools/constraints_measure.py`), five languages:

| what | result |
|---|---|
| code reading, 53 requests (incl. 16 that must read nothing) | precision 43/44, recall 43/43 |
| code checks on 42 replies (18 violations) | violations found 18/18, no false violation, 0 undecided |
| code checks on 26 file cases (PPTX, DOCX, PDF, HTML, SVG, XLSX; 10 violations) | violations found 10/10, no false violation |
| 19 checks on 7 files built by Atlas's renderer (3 decks, 2 DOCX, 2 PDF; 7 violations) | violations found 7/7, no false violation |
| time | reply check 0.04 to 0.2 ms; file view 2 to 6 ms per file |

The one extra reading: "ohne Einleitung" is also read as "nothing else" by
only_request, as its phrase tables intend.

Live, `full` mode, 12 fictional turns in five languages (wrong replies
written on purpose), staging gateway, `atlas-mistral` as the turn's profile
and the fast check profile first, after the fixes this measurement led to:

| what | result |
|---|---|
| model calls per turn with constraints | 1 to 5 (reading, verifier, repair; a call retried on the second profile counts) |
| added time per turn | 0.5 to 13.8 s, mean 4.7 s; the three slowest turns include provider 504 timeouts of the fast profile |
| added cost per turn | 0.0003 to 0.0013 EUR, mean 0.0006 EUR |
| replies that broke a constraint | 11: 9 meet every constraint after the pass, 2 say what is not met (a list of five steps for "at most four"; a reorder the rewrite did not make) |
| unchecked | 1 verifier item (call timed out) |
| missed by the reading | "no numbers" (asked in French) in one turn |

Model readings vary between runs on the same message (a checkbox style read
in one run, not in the next): the code reading stays the floor.

Hold rate (share of messages whose reply is held), measured on message sets
without storing their text: on 159 everyday requests, 0 % in `code` mode and
10 % in `full` mode; on a set of 69 form-heavy requests, 17 % and 90 %.

Earlier live runs found and fixed: list numbers counted as new figures (a
correct rewrite was refused), a count of items hidden by a wrong list style,
"no explanations" read as a forbidden word, a model's statement in English
for a Spanish request (the note now quotes the person), a paragraph count
read as a list count.

## 7. Bounds

- `code`: no model call, no cost; checks are pure functions of the reply.
- `full`: at most one reading, one verifier and one repair call per turn,
  each through the gateway (open models only, strict privacy passed on),
  reasoning off, temperature 0, 0.01 EUR cap per call, 12 s, 12 s and 25 s,
  all within the turn's remaining time minus 5 s and 40 s at most. A failed
  call never blocks the turn. Their cost is added to the turn's cost.
- Every checked turn logs one `answer_constraints` JSON line, counts only
  (section 4 bis).

## 8. Failure modes

| failure | effect | handling |
|---|---|---|
| model invents a constraint | right reply repaired | quote and exact texts must be the person's |
| model reads a content wish as a form constraint | false failure | forbidden words must be quoted; part-of-reply starts go to the verifier; contradictions resolved before checks |
| verifier passes without reading | false pass | verdict needs an evidence quotation found in the reply |
| verifier fails without reason | needless repair | same rule: no evidence, no verdict |
| repair breaks something | worse reply | kept only with strictly more passes, none lost, and accepted by `refusal` |
| count of one part read as the whole reply's | false gap | part-scoped: per part when delimited, else undecided |
| reply written by code for a stopped turn | invented answer | not checked nor rewritten |
| repair invents a fact or drops a link | wrong reply | refused by code |
| constraint of an earlier turn applied to a later reply | wrong repair | answer constraints bind their own turn unless said to last |
| reply already streamed | no rewrite possible | closing sentence only |
| provider slow | added wait | fast profile first, each call bounded, skipped below 4 s left |

## 9. Next

1. Run `full` on staging, compare the hold rate and the added time with the
   figures above, then decide the default.
2. Measure the layout checks live on staging: how often the extraction
   gives a layout rule, and how often a refusal for one is repaired. Read
   back `bulleted` from a delivered deck so that a revision keeps it
   without the ledger.
3. Prevent rather than report: a `no_new_file` constraint read before the
   turn routes (deferred_intent covers the main phrases already).
4. Strict privacy: run the reading and the verifier on the owned model, and
   measure a small open evaluator (Prometheus 2) as verifier.
5. Widen the code reading from the logged `judge` statements (which kinds
   recur), keeping precision first.
