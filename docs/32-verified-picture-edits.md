# 32. Verified picture edits: the edit contract for generated artifacts

Status: implemented for CPU pictures (`services/orchestrator/pictures/`). The same contract is meant for documents and apps.

## The defect class

A picture request with a hard constraint (a count, a presence or absence, a
colour, words to show) and its corrections over several turns could loop:

- pictures that failed the automatic check were shown as the answer, with a
  hedge;
- a complaint that only said what was wrong was read as a plain retry, and the
  same description was drawn again with a new seed;
- a correction the description already held changed nothing in it, so it was
  another random draw presented as the answer to the correction;
- a proposal Atlas made after looking at the picture was lost when the person
  accepted it;
- the source checks meant for answers grounded in documents or web pages read
  the code-written picture answer, rewrote it into a restatement of the
  request and noted Atlas's own labels (the artifact's version) as unsourced;
- the fixed texts used the polite form whatever the person's register.

## Survey (owner rule: look at what exists first)

- Diffusion models do not count. T2ICountBench ("Text-to-Image Diffusion
  Models Cannot Count", arXiv 2503.06884) finds that every model tested fails
  to produce the requested number of objects, worse as the number grows, and
  that prompt refinement does not fix it. GenEval (arXiv 2310.11513) and
  T2I-CompBench measure the same weakness. So rewording the prompt is not a
  fix; the count has to be enforced from outside the model.
- Generate, verify, retry. TIFA (arXiv 2303.11897) scores faithfulness by
  asking a visual question-answering model questions derived from the prompt.
  Self-correcting LLM-controlled Diffusion (SLD, CVPR 2024, arXiv 2311.16090,
  github.com/tsunghan-wu/SLD) generates, detects objects, compares with the
  prompt and corrects, training-free and generator-agnostic. CountLoop (arXiv
  2508.16644) loops with a critic but counts with a detector, because VLMs
  alone miscount dense scenes; ABACUS (arXiv 2606.23835) argues that external
  VLM critics bound the loop's reliability.
- Layout and region conditioning. LLM-grounded Diffusion (arXiv 2305.13655)
  and GLIGEN (arXiv 2301.07093) draw from boxes so each instance has a place;
  CountGen / "Make It Count" (CVPR 2025, arXiv 2406.10210) separates instances
  during denoising and corrects over- and under-generation (50 % count accuracy
  on CoCoCount against 25 % for the best baseline). These need control of the
  denoising loop or a grounded model.
- Inpainting with a mask edits one region and keeps the rest; it needs a
  server that edits (`cpu_server.edits`) and a mask, which our server does not
  take yet.
- Drawing by code (our painting-by-code path, docs/21) names every counted
  part in its own SVG group and its judge counts the groups, so a count can be
  measured exactly before the picture is shown.

What fits our constraints (open models, CPU image server, no control of the
denoising loop): generate-then-verify with the vision check we already run,
bounded retries, and a change of route to drawing by code. Layout conditioning
and masked inpainting are the next steps if the image server gains them; the
VLM-critic limit (CountLoop, ABACUS) is why only a positive check counts as a
pass and the gap the check saw is said, not hidden.

## The contract

A. An edit turn either changes the artifact's specification and makes a new
   version from it, or says plainly that it could not and why. A correction
   that leaves the description unchanged and has nothing to check makes no new
   version (`cpu_not_applied`). A correction already held by the description
   ("exactly N") is redrawn and verified, and said as such
   (`cpu_again_checked`), never "nothing precise to change". Proposals are
   state: the review's plan is stored with the picture (`PictureSet.proposed`)
   and an acceptance applies it even when the reading call finds nothing.
   The statements the current picture failed are state too
   (`Candidate.unmet`, `PictureSet.unmet`): a complaint that names no change
   corrects them (`cpu_turn.CORRECTION`).
B. Verify before showing. The rewrite turns counts, colours and unusual
   features of the cumulative request into checkable statements; every picture
   is checked by the vision call; while none passes, more seeds are drawn
   (`checked_pictures`, up to `MAX_ATTEMPTS`, within the server's own queue
   estimate and the turn's deadline). Only passing pictures are shown. When
   none passes, a drawing by code is tried (first turn: `turn._code_instead`)
   and kept only if it passes the same check; otherwise the closest attempt is
   shown and said to fail, with the gap the check saw ("the picture shows: two
   wheels") and the number of attempts. A picture that passed its check is never
   replaced by a new version that fails it (`cpu_kept`).
C. Loop breaker. A picture that ended a turn still missing a statement counts
   a miss (`PictureSet.misses`). The next correction of the same statement goes
   to drawing by code first and says that the method changed
   (`strategy_code`); the image model is not asked to repeat its failure.
D. A picture answer is written by code (`task_type` "drawing"): no
   source-faithfulness, quotation or unsourced-details check reads it
   (`chat_pipeline.SERVICE_WRITTEN`); Atlas's own artifact links, pictures and
   their labels are blanked before any detail extraction
   (`unsourced_details.OWN_LINK`). The fixed texts follow the person's register
   (`texts.text`, `packages.addressing`).

## Reuse for documents and apps

The same four parts map onto the expectations ledger (docs/31): the ledger's
requirements are the checkable statements, its check results are `unmet`, its
repair loop is the bounded retry, and a requirement missed twice in a row
should change route (a different writer prompt structure or a decomposition)
rather than repeat. The picture path keeps its statements with the artifact
version in the picture set for now; moving them into the ledger is the next
step.

## Costs and limits

A vision check costs about 0.0003 EUR on the gateway's locate role. Retries use
the fixed-cost CPU server (about 22 s per picture). A drawing by code costs the
painter's drafts (about 0.04 to 0.06 EUR) and only runs after every CPU attempt
failed. The vision model can miscount (a false pass was seen in the measure);
the answer never claims more than "an automatic check finds it".
