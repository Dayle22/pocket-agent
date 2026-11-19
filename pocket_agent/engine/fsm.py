"""Finite state machine mapping ACP agent lifecycle states to sprite actions."""

from __future__ import annotations

from enum import Enum, auto


class PetAction(Enum):
    """Row indices inside a standard 192x208 8x9 companion spritesheet."""

    IDLE = 0
    RUNNING_RIGHT = 1
    RUNNING_LEFT = 2
    WAVING = 3
    JUMPING = 4
    FAILED = 5
    WAITING = 6
    RUNNING = 7
    REVIEW = 8


class PetState(Enum):
    """High-level semantic states driven by ACP events and user interaction."""

    IDLE = auto()
    THINKING = auto()
    CODING = auto()
    PERMISSION = auto()
    CELEBRATE = auto()
    ERROR = auto()


STATE_TO_ACTION: dict[PetState, PetAction] = {
    PetState.IDLE: PetAction.IDLE,
    PetState.THINKING: PetAction.WAITING,
    PetState.CODING: PetAction.REVIEW,
    PetState.PERMISSION: PetAction.WAVING,
    PetState.CELEBRATE: PetAction.JUMPING,
    PetState.ERROR: PetAction.FAILED,
}
