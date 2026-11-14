"""Placeholder for the phase-10 preference and Timeline-Bench harnesses."""

from __future__ import annotations

import sys


def cli(name: str, argv: list[str]) -> int:
    print(f"'{name}' arrives with phase 10 (see docs/phases/PHASE-10-director-loop.md).", file=sys.stderr)
    return 2
