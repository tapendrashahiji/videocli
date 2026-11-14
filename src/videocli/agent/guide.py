"""``agent-guide`` and ``skill install``: the packaged skill text, written where agents find it."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from videocli import __version__
from videocli.core.errors import UsageError
from videocli.core.fsutil import atomic_write_text

SKILL_DIR = Path(__file__).parent / "skill"
STAMP_RE = re.compile(r"<!-- videocli-skill version=(\S+) -->")
START = "<!-- videocli:start -->"
END = "<!-- videocli:end -->"


def topics() -> list[str]:
    out = ["core"]
    out += sorted(p.stem for p in (SKILL_DIR / "topics").glob("*.md"))
    return out


def text(topic: str = "core") -> str:
    if topic == "core":
        return (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    p = SKILL_DIR / "topics" / f"{topic}.md"
    if not p.is_file():
        raise UsageError("BAD_ARGUMENT", f"Unknown topic {topic!r}.", hint=f"Topics: {', '.join(topics())}")
    return p.read_text(encoding="utf-8")


def _stamp() -> str:
    return f"<!-- videocli-skill version={__version__} -->\n"


def skill_path(target: str, scope: str, base: Path | None) -> Path:
    if target == "claude":
        root = (base or (Path.home() if scope == "user" else Path.cwd()))
        return root / ".claude" / "skills" / "videocli" / "SKILL.md"
    if target == "generic":
        return (base or Path.cwd()) / "AGENTS.md"
    raise UsageError("BAD_ARGUMENT", f"--target must be claude or generic (got {target!r}).")


def install(target: str = "claude", scope: str = "project", base: Path | None = None, dry_run: bool = False) -> dict[str, Any]:
    if scope not in ("project", "user"):
        raise UsageError("BAD_ARGUMENT", "--scope must be project or user.")
    path = skill_path(target, scope, base)
    body = text("core")
    if target == "claude":
        # Keep the front matter first; the version stamp follows it.
        if body.startswith("---"):
            end = body.index("---", 3) + 3
            content = body[:end] + "\n" + _stamp() + body[end:].lstrip("\n")
        else:  # pragma: no cover
            content = _stamp() + body
        # Topics travel with the skill so the agent can read them without the CLI.
        extra = {f"topics/{t}.md": text(t) for t in topics() if t != "core"}
    else:
        section = body.split("---", 2)[2].lstrip() if body.startswith("---") else body
        block = f"{START}\n{_stamp()}{section.rstrip()}\n{END}\n"
        existing = path.read_text(encoding="utf-8") if path.is_file() else ""
        if START in existing and END in existing:
            pre, rest = existing.split(START, 1)
            post = rest.split(END, 1)[1].lstrip("\n")
            content = pre + block + (("\n" + post) if post else "")
        else:
            content = existing + ("\n" if existing and not existing.endswith("\n\n") else "") + block
        extra = {}
    previous = path.read_text(encoding="utf-8") if path.is_file() else None
    changed = previous != content
    if not dry_run:
        if changed:
            atomic_write_text(path, content)
        for rel, t in extra.items():
            atomic_write_text(path.parent / rel, t)
    return {"path": str(path), "target": target, "scope": scope, "version": __version__, "changed": changed,
            "existed": previous is not None, "dry_run": dry_run, "topics": topics()}


def installed_skill_status() -> dict[str, Any] | None:
    """The first installed skill found (project, then user), with a staleness flag."""
    for scope in ("project", "user"):
        for target in ("claude", "generic"):
            p = skill_path(target, scope, None)
            if target == "generic" and scope == "user":
                continue
            if not p.is_file():
                continue
            m = STAMP_RE.search(p.read_text(encoding="utf-8", errors="replace"))
            if not m:
                continue
            ver = m.group(1)
            return {"path": str(p), "version": ver, "stale": ver != __version__, "target": target, "scope": scope}
    return None
