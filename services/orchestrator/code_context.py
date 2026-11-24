"""The code the conversation already holds, for "run it" and "run it with X changed".

A request to run "the snippet above", or to run it with one value changed,
names no file: the code is in an earlier message, the person's or the
assistant's. The Work step starts in an empty workspace, so the request is
completed with the latest code block of the conversation and the message it
came from. Two blocks: the latest wins and the step is told which one it is.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

MAX_CHARACTERS = 6000
_FENCED = re.compile(
    r"(?ms)^[ \t]*(`{3,}|~{3,})[ \t]*([\w+#.-]*)[^\n]*\n(.*?)^[ \t]*\1[ \t]*$"
)
# A line of code with no fence around it: an assignment, a call, a keyword
# statement, an indented body or a closing brace.
_CODE_LINE = re.compile(
    r"^\s*(?:"
    r"[A-Za-z_][\w.\[\]]*\s*(?:[-+*/%|&]?=|:=)\s*\S.*"
    r"|[A-Za-z_][\w.]*\(.*\)\s*;?"
    r"|(?:def|class|import|from|for|while|if|elif|else|try|except|return|print|const|let|var|"
    r"function|SELECT|INSERT|UPDATE|echo|cd|ls|cat|grep|pip|npm|git)\b.*"
    r"|[}\])]+;?"
    r"|\s{2,}\S.*"
    r")\s*$"
)


_LIST = re.compile(r"^\s*(?:[-*+•>]|\d+[.)])\s")
_PUNCTUATION = re.compile(r"[=()\[\]{}:;]")


def _bare_block(text: str) -> str:
    """The last run of unfenced code lines of a message: at least two lines,
    two of them with code punctuation, after the sentences that introduce them."""
    runs: list[list[str]] = []
    run: list[str] = []
    for line in [*text.splitlines(), "\u0000"]:
        if line.strip() and not _LIST.match(line) and _CODE_LINE.match(line):
            run.append(line)
        elif not line.strip() and run:
            continue
        else:
            if len(run) >= 2 and sum(bool(_PUNCTUATION.search(x)) for x in run) >= 2:
                runs.append(run)
            run = []
    return "\n".join(runs[-1]).strip() if runs else ""


def code_of(text: str) -> str:
    """The last code of a message: its last fenced block, else bare code lines."""
    fenced = _FENCED.findall(text)
    if fenced:
        return str(fenced[-1][2]).strip()
    return _bare_block(text)


def last_code(messages: Sequence[tuple[str, str]]) -> tuple[str, str, int] | None:
    """(role, code, messages back) of the latest code of a conversation."""
    for back, (role, text) in enumerate(reversed(messages), start=1):
        code = code_of(text)
        if code:
            return role, code[:MAX_CHARACTERS], back
    return None


def code_hint(messages: Sequence[tuple[str, str]], current: str) -> str:
    """The note that hands the conversation's latest code to the Work step.

    Empty when the request carries its own code or the conversation has none.
    `messages` are the earlier turns, oldest first, as (role, text).
    """
    if code_of(current):
        return ""
    found = last_code(messages)
    if found is None:
        return ""
    role, code, _ = found
    owner = "the person's" if role == "user" else "your own"
    return (
        f"\n\n(The latest code of this conversation, from {owner} earlier message, "
        "is below. A request to run it, or to run it with a value changed, means "
        "this code: change what was asked and run it, and name the code used if "
        "the conversation holds several. Do not ask for it again.)\n"
        f"```\n{code}\n```"
    )
