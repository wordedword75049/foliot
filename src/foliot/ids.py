"""Public identity types used by the simulation engine."""

from typing import NewType

__all__ = ["EntityId", "SuspensionId", "Tick"]

type Tick = int
"""A zero-based logical tick number.

`Tick` is a type alias, so runtime values are ordinary integers. It documents
logical time without adding conversion or serialization overhead.
"""

EntityId = NewType("EntityId", str)
"""Opaque identity of the entity that owns an action.

The queue groups actions by owner for suspension and cleanup. Random-stream
derivation also includes this value. It does not represent an action target.
"""

SuspensionId = NewType("SuspensionId", str)
"""Opaque identity of whatever suspended an action.

Resumption uses the same handle to wake matching actions. The optional Event
layer uses an Event id; Layer-1 applications may define any stable handle.
"""
