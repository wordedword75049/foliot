"""Identities the engine reads.

Every name here passes the membership test of DESIGN_SNAPSHOT §3: the engine
reads it. Anything the engine does not read is game payload and does not
belong in this package.
"""

from typing import NewType

__all__ = ["EntityId", "SuspensionId", "Tick"]

type Tick = int
"""A tick number.

An alias, not a distinct type: `Tick` *is* `int` and the two are freely
interchangeable. It buys readability and no safety at all. Where confusing two
values would be costly, use `NewType` as below.
"""

EntityId = NewType("EntityId", str)
"""Who owns an action.

Distinct from `str` on purpose: the queue indexes on it and the RNG seeds on it
(§9.1), so passing the wrong string here would silently misattribute both.
"""

SuspensionId = NewType("SuspensionId", str)
"""What suspended something, and therefore what owes it a wake-up (§6.2).

Opaque: the engine only ever groups by it. Layer 2 passes an event id; a
layer-1-only game may pass any string. Distinct from `EntityId` so that
`resume(entity_id)` cannot type-check -- it would match no suspension, wake
nobody, and leave the activity asleep forever with nothing raised.
"""
