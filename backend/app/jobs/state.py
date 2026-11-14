"""The job state machine. All status changes go through check_transition."""

from __future__ import annotations

from ..models.job import JobStatus as S

ALLOWED: dict[S, frozenset[S]] = {
    S.QUEUED: frozenset({S.PLANNING, S.FAILED, S.CANCELLED}),
    S.PLANNING: frozenset({S.AWAITING_CONFIRMATION, S.FAILED, S.CANCELLED}),
    S.AWAITING_CONFIRMATION: frozenset({S.APPLYING, S.CANCELLED, S.FAILED}),
    S.APPLYING: frozenset({S.DONE, S.FAILED}),
    S.DONE: frozenset(),
    S.FAILED: frozenset(),
    S.CANCELLED: frozenset(),
}


class InvalidTransition(Exception):
    def __init__(self, current: S, new: S) -> None:
        super().__init__(f"cannot go from {current.value} to {new.value}")
        self.current = current
        self.new = new


def check_transition(current: S, new: S) -> None:
    if new not in ALLOWED[current]:
        raise InvalidTransition(current, new)
