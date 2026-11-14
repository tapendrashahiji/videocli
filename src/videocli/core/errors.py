"""Error tree, exit codes and the error-code registry (SPEC §6.2).

Every failure the tool reports is a :class:`VideoCliError`. ``cli.app.main`` is the only
place that catches them and turns them into the error envelope and an exit status.
"""

from __future__ import annotations

from typing import Any

#: One registry of every error code with a one-line description. Exported by
#: ``videocli commands`` so agents can look codes up.
ERROR_CODES: dict[str, str] = {
    # 130: interrupted
    "INTERRUPTED": "The command was interrupted; re-run it to resume where it stopped",
    # 1: runtime failure
    "INTERNAL_ERROR": "An unexpected error; the log file named in details has the traceback",
    "FFMPEG_FAILED": "ffmpeg or ffprobe exited with an error",
    "ANALYZER_FAILED": "An analyzer failed while running",
    "BAKE_FAILED": "A baker produced no output or broke the bake contract",
    "RENDER_FAILED": "A render node failed",
    "DOWNLOAD_FAILED": "A download during setup failed or did not match its checksum",
    "INSTALL_FAILED": "Installing a dependency group failed",
    # 2: usage error
    "BAD_ARGUMENT": "An argument or option is missing or invalid",
    "COMPARE_NOT_SYNCABLE": "The EDL is not a straight cut-down, so compare cannot line it up with its source; use --drift",
    "FEATURE_NOT_AVAILABLE": "The request needs a feature that a later phase delivers",
    "NOT_SUPPORTED": "The command does not support this mode (for example --estimate)",
    # 3: missing dependency
    "FFMPEG_NOT_FOUND": "No usable ffmpeg or ffprobe was found",
    "FFMPEG_TOO_OLD": "ffmpeg is older than the minimum version or lacks a required feature",
    "MODEL_NOT_FOUND": "A required model is not downloaded",
    "FEATURE_NOT_INSTALLED": "An optional dependency group is not installed",
    # 4: bad input
    "FILE_NOT_FOUND": "An input file does not exist",
    "NOT_A_MEDIA_FILE": "ffprobe could not read the input as media",
    "NO_AUDIO": "The source has no audio stream",
    "NO_VIDEO": "The source has no video stream",
    "EDL_INVALID": "The EDL file failed validation",
    "EDL_NOT_FOUND": "No EDL with that name exists in the workspace",
    "EDL_EXISTS": "An EDL with that name already exists",
    "ITEM_NOT_FOUND": "No clip or item with that id exists in the EDL",
    "SOURCE_CHANGED": "A source file changed since it was registered",
    "SOURCE_UNKNOWN": "The source is not registered in this workspace",
    "ARTIFACT_MISSING": "A required analysis artifact is missing; run analyze first",
    "WORKSPACE_LOCKED": "Another videocli process holds the workspace lock",
    "WORKSPACE_INVALID": "The workspace is unreadable or has an unknown schema version",
    "OUTPUT_IS_SOURCE": "Refusing to overwrite a source file",
    "OUTPUT_EXISTS": "The output file exists; pass --force to overwrite",
    "SCHEMA_INVALID": "A data file failed schema validation",
    "ORDER_NOT_FOUND": "No work order with that id exists",
    "ANSWER_INVALID": "A submitted answer does not match the order's schema",
    "TEMPLATE_INVALID": "A graphics template manifest or parameter is invalid",
    "PHRASE_NOT_FOUND": "A phrase could not be found in the transcript",
    # 5: checks failed
    "VERIFY_FAILED": "One or more verify checks failed",
    "LINT_FAILED": "EDL lint found errors",
    "AUDIT_FAILED": "Audit found issues at or above the --fail-on level",
    "GFX_LINT_FAILED": "Graphics lint found errors",
    # 6: waiting on an answer
    "ORDERS_PENDING": "Work orders are waiting for answers; submit them and re-run",
    "REVIEW_PENDING": "A human review is waiting; ingest notes and re-run",
}


class VideoCliError(Exception):
    """Base class for every reported failure."""

    exit_code: int = 1

    def __init__(
        self,
        code: str,
        message: str,
        hint: str | None = None,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        if code not in ERROR_CODES:  # pragma: no cover - guards against typos in new code
            raise ValueError(f"unregistered error code: {code}")
        self.code = code
        self.message = message
        self.hint = hint
        self.retryable = retryable
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "hint": self.hint,
            "retryable": self.retryable,
            "details": self.details,
        }


class RuntimeFailure(VideoCliError):
    exit_code = 1


class UsageError(VideoCliError):
    exit_code = 2


class DependencyError(VideoCliError):
    exit_code = 3


class InputError(VideoCliError):
    exit_code = 4


class CheckFailed(VideoCliError):
    exit_code = 5


class Pending(VideoCliError):
    exit_code = 6


def file_not_found(path: str) -> InputError:
    return InputError("FILE_NOT_FOUND", f"No such file: {path}", hint="Check the path and try again.")


def feature_not_installed(group: str, what: str) -> DependencyError:
    return DependencyError(
        "FEATURE_NOT_INSTALLED",
        f"{what} needs the optional '{group}' group, which is not installed.",
        hint=f"Run: videocli setup --with {group}",
        details={"group": group},
    )


def feature_not_available(what: str, phase: int) -> UsageError:
    return UsageError(
        "FEATURE_NOT_AVAILABLE",
        f"{what} is not renderable yet (planned for phase {phase}).",
        hint="Remove that part of the EDL or wait for a release that supports it.",
        details={"phase": phase},
    )
