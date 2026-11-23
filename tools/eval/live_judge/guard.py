"""Staging-only guard: the live judge refuses to start anywhere else.

Fail closed. The instance must opt in (ATLAS_LIVE_QUALITY_JUDGE=1, set only by
infra/instances/staging.env), and none of its identity values (port base, data
directory, interaction logs, public address) may equal the production overlay's.
The production overlay and the shared layer must never carry the flag.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

FLAG = "ATLAS_LIVE_QUALITY_JUDGE"
IDENTITY = (
    "ATLAS_PORT_BASE",
    "ATLAS_DATA_DIR",
    "ATLAS_INTERACTION_DIR",
    "ATLAS_PUBLIC_URL",
)
REPO = Path(__file__).resolve().parents[3]


class NotStaging(Exception):
    pass


def parse_env(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key.strip()] = value
    return out


def check(env: Mapping[str, str] | None = None, repo: Path = REPO) -> dict[str, str]:
    """Return the instance identity when this is a non-production instance that opted in."""
    env = os.environ if env is None else env
    if env.get(FLAG) != "1":
        raise NotStaging(f"{FLAG}=1 is not set: the live judge runs on staging only")
    instances = repo / "infra" / "instances"
    try:
        production = parse_env((instances / "production.env").read_text("utf-8"))
        common = parse_env((instances / "common.env").read_text("utf-8"))
    except OSError as exc:
        raise NotStaging(
            f"cannot read the production overlay ({exc}): refused"
        ) from None
    if FLAG in production or FLAG in common:
        raise NotStaging(
            f"{FLAG} appears in production.env or common.env: refused everywhere"
        )
    identity: dict[str, str] = {}
    for key in IDENTITY:
        value = env.get(key, "").strip().rstrip("/")
        if not value:
            raise NotStaging(f"{key} is not set: cannot tell this instance apart")
        if value == production.get(key, "").strip().rstrip("/"):
            raise NotStaging(f"{key} equals the production value: refused")
        identity[key] = value
    return identity
