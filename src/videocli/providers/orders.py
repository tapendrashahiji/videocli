"""Work orders (SPEC §6.5, §10.2): structured questions that someone with eyes or ears answers.

A command that needs judgement creates an order. The ``agent`` provider leaves it pending: the
command exits 6 with ``ORDERS_PENDING``; the calling agent reads the images, submits answers with
``videocli orders submit``, and re-runs the command, which then proceeds. Every stored answer
records its provider and model, so answers from different providers are never mixed silently.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from videocli.core.cache import make_key
from videocli.core.errors import InputError, Pending, UsageError
from videocli.core.fsutil import read_json, write_json
from videocli.core.workspace import Workspace, now_iso

SCHEMA = "videocli.order/v1"


def orders_dir(ws: Workspace) -> Path:
    return ws.dir("orders")


def _path(ws: Workspace, oid: str) -> Path:
    return orders_dir(ws) / f"{oid}.json"


def load(ws: Workspace, oid: str) -> dict[str, Any]:
    p = _path(ws, oid)
    if not p.is_file():
        raise InputError("ORDER_NOT_FOUND", f"No work order {oid!r}.", hint="Run: videocli orders list")
    return read_json(p)


def save(ws: Workspace, order: dict[str, Any]) -> Path:
    p = _path(ws, order["id"])
    write_json(p, order)
    return p


def all_orders(ws: Workspace) -> list[dict[str, Any]]:
    d = ws.root / "orders"
    return [read_json(p) for p in sorted(d.glob("ord_*.json"))] if d.is_dir() else []


def create(ws: Workspace, kind: str, items: list[dict[str, Any]], *, meta: dict[str, Any] | None = None, brief: str = "") -> dict[str, Any]:
    """Create (or find) the order for exactly these items; re-running a command reuses its order."""
    key = make_key(kind, [(i["id"], i.get("question"), i.get("images"), i.get("answer_schema")) for i in items])[:16]
    for o in all_orders(ws):
        if o.get("key") == key and o.get("status") != "cancelled":
            return o
    n = len(all_orders(ws)) + 1
    oid = f"ord_{datetime.now().strftime('%Y%m%d')}_{n:04d}"
    order = {"schema": SCHEMA, "id": oid, "key": key, "kind": kind, "created": now_iso(), "status": "pending",
             "brief": brief, "items": items, "answers": {}, "meta": meta or {}}
    save(ws, order)
    return order


def open_items(order: dict[str, Any]) -> list[dict[str, Any]]:
    return [i for i in order["items"] if i["id"] not in order.get("answers", {})]


def pending_error(order: dict[str, Any], rerun: str) -> Pending:
    n = len(open_items(order))
    return Pending(
        "ORDERS_PENDING",
        f"{n} item(s) in work order {order['id']} ({order['kind']}) are waiting for answers.",
        hint=f"Run: videocli orders show {order['id']}   then: videocli orders submit {order['id']} answers.jsonl   then re-run: {rerun}",
        details={"order": order["id"], "kind": order["kind"], "open": n, "rerun": rerun},
    )


# --------------------------------------------------------------------------- answer validation


def _check_value(spec: Any, v: Any) -> str | None:
    if isinstance(spec, dict):
        if not isinstance(v, dict):
            return "expected an object"
        for k, sub in spec.items():
            opt = k.endswith("?")
            name = k.rstrip("?")
            if name not in v:
                if not opt:
                    return f"missing '{name}'"
                continue
            err = _check_value(sub, v[name])
            if err:
                return f"{name}: {err}"
        return None
    if isinstance(spec, list):
        if not isinstance(v, list):
            return "expected a list"
        for x in v:
            err = _check_value(spec[0], x) if spec else None
            if err:
                return err
        return None
    s = str(spec)
    if s == "string":
        return None if isinstance(v, str) and v.strip() else "expected a non-empty string"
    if s == "boolean":
        return None if isinstance(v, bool) else "expected true or false"
    m = re.fullmatch(r"number(?::(-?[\d.]+)-(-?[\d.]+))?", s)
    if m:
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            return "expected a number"
        if m.group(1) and not (float(m.group(1)) <= v <= float(m.group(2))):
            return f"expected {m.group(1)}..{m.group(2)}"
        return None
    if "|" in s or s.isupper():
        choices = s.split("|")
        return None if v in choices else f"expected one of {', '.join(choices)}"
    return None


def validate_answer(schema: dict[str, Any], answer: Any) -> str | None:
    return _check_value(schema, answer)


def submit(ws: Workspace, oid: str, lines: list[str], *, provider: str = "agent", model: str | None = None) -> dict[str, Any]:
    order = load(ws, oid)
    if order["status"] == "cancelled":
        raise UsageError("BAD_ARGUMENT", f"Order {oid} was cancelled.")
    items = {i["id"]: i for i in order["items"]}
    accepted, rejected = [], []
    for n, line in enumerate(lines, 1):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as exc:
            rejected.append({"line": n, "error": f"invalid JSON: {exc.msg}"})
            continue
        iid = rec.get("id")
        if iid not in items:
            rejected.append({"line": n, "id": iid, "error": "unknown item id"})
            continue
        ans = rec.get("answer", {k: v for k, v in rec.items() if k not in ("id", "model", "provider")})
        err = validate_answer(items[iid].get("answer_schema") or {}, ans)
        if err:
            rejected.append({"line": n, "id": iid, "error": err})
            continue
        order["answers"][iid] = {"answer": ans, "provider": rec.get("provider", provider), "model": rec.get("model", model), "at": now_iso()}
        accepted.append(iid)
    if not open_items(order):
        order["status"] = "answered"
    save(ws, order)
    res = {"order": oid, "accepted": len(accepted), "rejected": rejected, "open": len(open_items(order)), "status": order["status"]}
    if rejected:
        raise InputError("ANSWER_INVALID", f"{len(rejected)} answer(s) rejected; {len(accepted)} stored.",
                         hint="Fix the listed lines and submit them again (accepted answers are kept).", details=res)
    return res


def answers(order: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return order.get("answers", {})


# --------------------------------------------------------------------------- providers


class AgentProvider:
    """The calling agent answers: the order is written and the command waits (exit 6)."""

    name = "agent"

    def can_answer(self, kind: str) -> bool:
        return True

    def answer(self, ws: Workspace, order: dict[str, Any], rerun: str) -> dict[str, Any]:
        if open_items(order):
            raise pending_error(order, rerun)
        return order


class LocalProvider:
    """A local model answers (``perception`` group)."""

    name = "local"

    def can_answer(self, kind: str) -> bool:
        return False

    def answer(self, ws: Workspace, order: dict[str, Any], rerun: str) -> dict[str, Any]:
        from videocli.setup.profiles import require_group

        require_group("perception", f"Answering '{order['kind']}' orders with a local model")
        from videocli.core.errors import DependencyError

        raise DependencyError("MODEL_NOT_FOUND", f"No local model is configured for '{order['kind']}' orders.",
                              hint="Use --provider agent (the default), or see docs/decisions.md X3.")


class ApiProvider:
    """Opt-in API provider; off unless a key is configured. Never sends anything silently."""

    name = "api"

    def can_answer(self, kind: str) -> bool:
        return False

    def answer(self, ws: Workspace, order: dict[str, Any], rerun: str) -> dict[str, Any]:
        from videocli.core.errors import DependencyError

        raise DependencyError("FEATURE_NOT_INSTALLED", "The api provider is not configured (off by default).",
                              hint="Use --provider agent; api support needs an explicit key and is opt-in.",
                              details={"would_send": {"order": order["id"], "items": len(order["items"]),
                                                      "images": sum(len(i.get("images", [])) for i in order["items"])}})


PROVIDERS = {"agent": AgentProvider(), "local": LocalProvider(), "api": ApiProvider()}


def provider(name: str | None) -> Any:
    n = name or "agent"
    if n not in PROVIDERS:
        raise UsageError("BAD_ARGUMENT", f"Unknown provider {n!r}.", hint="Use agent, local or api.")
    return PROVIDERS[n]
