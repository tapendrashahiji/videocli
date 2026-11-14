"""Generate docs/commands.md and docs/schemas/ from the live CLI (``python scripts/gen_docs.py``)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    from videocli.agent import schemas
    from videocli.agent.introspect import describe
    from videocli.cli.app import app

    d = describe(app)
    lines = ["# Command reference", "", "Generated from `videocli commands` by `scripts/gen_docs.py`. Do not edit by hand.", ""]
    lines += ["## Global options", "", "| Option | Type | Help |", "|---|---|---|"]
    for o in d["global_options"]:
        lines.append(f"| `{o['name']}` | {o['type']} | {o.get('help', '')} |")
    lines.append("")
    for c in d["commands"]:
        lines += [f"## `videocli {c['name']}`", "", c["summary"], ""]
        flags = []
        if c["writes"]:
            flags.append("writes")
        if c["writes"] and c["dry_run"]:
            flags.append("`--dry-run`")
        if c["estimate"]:
            flags.append("`--estimate`")
        lines.append(f"Exit codes: {', '.join(map(str, c['exit_codes']))}" + (f" · supports {', '.join(flags)}" if flags else ""))
        lines.append("")
        if c["args"] or c["options"]:
            lines += ["| Name | Type | Required | Help |", "|---|---|---|---|"]
            for p in c["args"] + c["options"]:
                name = p["name"] + (f" {p['metavar']}" if p.get("metavar") and p["type"] != "flag" else "")
                lines.append(f"| `{name}` | {p['type']} | {'yes' if p['required'] else ''} | {p.get('help', '')} |")
            lines.append("")
    lines += ["## Error codes", "", "| Code | Meaning |", "|---|---|"]
    for code, desc in d["error_codes"].items():
        lines.append(f"| `{code}` | {desc} |")
    (ROOT / "docs" / "commands.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    out = ROOT / "docs" / "schemas"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for name in schemas.models():
        fn = name.replace("command:", "command-").replace(" ", "-") + ".schema.json"
        (out / fn).write_text(json.dumps(schemas.json_schema(name), indent=2) + "\n", encoding="utf-8")
    print(f"wrote docs/commands.md ({d['total']} commands) and {len(schemas.models())} schemas")


if __name__ == "__main__":
    main()
