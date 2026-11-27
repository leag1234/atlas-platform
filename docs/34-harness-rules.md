# Harness Rules

Code does not read meaning. It measures, executes, renders, counts, enforces
budgets, privacy and protocols. Meaning is read by the model. A word list, a
regular expression applied to a person's sentence, or a branch for one case is
not knowledge: it is the next special case.

Know-how learnt from a defect is written as a skill, not as a code rule. The
accepted changes are:

- a general mechanism;
- a measuring instrument;
- a skill text;
- the repair of an outage;
- a deletion.

`tools/harness_rules.py` counts three lexical-rule kinds in Python files under
`services/orchestrator/` and `packages/`:

- `regex`: calls to the standard `re` module or the third-party `regex` module
  and their aliases, for the compile, search, match, fullmatch, find, replace
  and split operations;
- `word_tables`: assignments, at any level, to collection
  literals, or calls that build such collections, containing at least three
  string literals;
- `language_tables`: dictionary literals with at least two language-code keys.

The command prints totals and the ten largest files per kind. `--check`
compares the totals with `tools/harness_rules.json` and fails when a total is
above it, naming the files that grew. `--update` rewrites the baseline; a rise
requires `--reason` and is recorded in the baseline's `raised` list. A decrease
needs no reason. `tools/prepush.py` runs the same check before every push. A total below
the baseline is a warning that asks to lock the lower value with `--update`.
