"""Progress events on stderr: ``bar``, ``json`` (newline-delimited) or ``none`` sinks.

Domain code calls :func:`step` (decorator or context manager) and :func:`update`. The CLI
chooses the sink once per process with :func:`configure`.
"""

from __future__ import annotations

import contextlib
import functools
import json
import sys
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


class Sink:
    def event(self, ev: dict[str, Any]) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class NoneSink(Sink):
    def event(self, ev: dict[str, Any]) -> None:
        return None


class JsonSink(Sink):
    def __init__(self, stream=None):
        self.stream = stream or sys.stderr
        self.lock = threading.Lock()

    def event(self, ev: dict[str, Any]) -> None:
        with self.lock:
            self.stream.write(json.dumps(ev, ensure_ascii=False) + "\n")
            self.stream.flush()


class BarSink(Sink):
    """A minimal single-line bar; avoids importing rich on hot paths."""

    def __init__(self, stream=None):
        self.stream = stream or sys.stderr
        self.lock = threading.Lock()
        self.last = 0.0
        self.active = False

    def _line(self, text: str) -> None:
        self.stream.write("\r\x1b[2K" + text)
        self.stream.flush()

    def event(self, ev: dict[str, Any]) -> None:
        with self.lock:
            kind = ev["event"]
            if kind == "started":
                self._line(f"{ev['step']} …")
                self.active = True
            elif kind == "progress":
                now = time.monotonic()
                if now - self.last < 0.1 and ev.get("fraction", 0) < 1:
                    return
                self.last = now
                msg = f" {ev['message']}" if ev.get("message") else ""
                if ev.get("fraction") is None:  # size unknown: show activity, not a fake bar
                    self._line(f"{ev['step']} …{msg}")
                    return
                frac = max(0.0, min(1.0, ev["fraction"]))
                width = 28
                filled = int(frac * width)
                self._line(f"{ev['step']} [{'#' * filled}{'.' * (width - filled)}] {frac * 100:5.1f}%{msg}")
            elif kind == "finished":
                self._line(f"{ev['step']} done in {ev.get('seconds', 0):.1f}s\n")
                self.active = False
            elif kind == "failed":
                self._line(f"{ev['step']} failed\n")
                self.active = False
            elif kind == "message":
                if self.active:
                    self.stream.write("\n")
                self.stream.write(ev.get("message", "") + "\n")
                self.stream.flush()


_sink: Sink = NoneSink()
_local = threading.local()


def configure(mode: str) -> None:
    """``bar``, ``json``, ``none`` or ``auto`` (bar on a terminal, else none)."""
    global _sink
    if mode == "auto":
        mode = "bar" if sys.stderr.isatty() else "none"
    _sink = {"bar": BarSink, "json": JsonSink, "none": NoneSink}.get(mode, NoneSink)()


def emit(ev: dict[str, Any]) -> None:
    ev.setdefault("time", round(time.time(), 3))
    _sink.event(ev)


def _stack() -> list[str]:
    if not hasattr(_local, "stack"):
        _local.stack = []
    return _local.stack


@contextlib.contextmanager
def step_ctx(name: str, **fields: Any) -> Iterator[None]:
    stack = _stack()
    stack.append(name)
    t0 = time.monotonic()
    emit({"event": "started", "step": name, **fields})
    try:
        yield
    except BaseException:
        emit({"event": "failed", "step": name})
        raise
    else:
        emit({"event": "finished", "step": name, "seconds": round(time.monotonic() - t0, 3)})
    finally:
        stack.pop()


def step(name: str) -> Callable[[F], F]:
    """Decorator: emit ``started``, ``finished`` (or ``failed``) around a function."""

    def deco(fn: F) -> F:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with step_ctx(name):
                return fn(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return deco


def update(fraction: float | None = None, message: str | None = None, step_name: str | None = None, **fields: Any) -> None:
    """Emit a progress event. Extra ``fields`` (e.g. ``bytes_done``) go into JSON events as-is."""
    stack = _stack()
    name = step_name or (stack[-1] if stack else "work")
    ev: dict[str, Any] = {"event": "progress", "step": name, **fields}
    if fraction is not None:
        ev["fraction"] = round(max(0.0, min(1.0, fraction)), 4)
    if message:
        ev["message"] = message
    emit(ev)


def message(text: str) -> None:
    emit({"event": "message", "message": text})
