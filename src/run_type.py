"""Execution/result run-mode identity shared across ATS boundaries."""

from enum import StrEnum


class RunType(StrEnum):
    """Identify the evidence domain that produced an ATS state or outcome."""

    LIVE = "LIVE"
    PAPER = "PAPER"
    REPLAY = "REPLAY"


LIVE_VALIDATION_RUN_TYPES = frozenset({RunType.LIVE})


def is_live_validation_run_type(run_type: RunType) -> bool:
    """Return whether a run type may contribute to live validation evidence."""

    return run_type in LIVE_VALIDATION_RUN_TYPES
