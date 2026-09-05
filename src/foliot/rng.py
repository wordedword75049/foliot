"""Addressable deterministic randomness.

Each action occurrence receives its own reproducible stream. Unrelated actions
therefore cannot change one another's rolls merely by running first.
"""

from hashlib import blake2b
from secrets import randbits
from typing import Protocol

from foliot.ids import EntityId, Tick

__all__ = ["Rng", "counter_rng", "new_world_seed"]

_UINT64_SIZE = 1 << 64
_UINT64_MASK = _UINT64_SIZE - 1
_UINT128_SIZE = 1 << 128
_FLOAT_UNIT = 1.0 / (1 << 53)

_SPLITMIX_INCREMENT = 0x9E3779B97F4A7C15
_SPLITMIX_MULTIPLIER_1 = 0xBF58476D1CE4E5B9
_SPLITMIX_MULTIPLIER_2 = 0x94D049BB133111EB
_PERSONALIZATION = b"foliot-rng-v1"
_EVENT_PERSONALIZATION = b"foliot-event-v1"


class Rng(Protocol):
    """A deterministic stream supplied to one decision.

    Action streams are bound to `(world_seed, entity_id, tick, seq)`. Consumers
    normally receive an `Rng` through `TickContext` or an Event context rather
    than constructing it directly.
    """

    def random(self) -> float:
        """Uniform in [0.0, 1.0)."""
        ...

    def below(self, n: int, /) -> int:
        """Return an unbiased integer in ``range(n)``.

        Args:
            n: Exclusive upper bound. Must satisfy ``1 <= n <= 2**64``.

        Raises:
            TypeError: If `n` is not an integer or is a boolean.
            ValueError: If `n` is outside the supported range.
        """
        ...


def new_world_seed() -> int:
    """Return a securely generated unsigned 128-bit world seed.

    Call this once when creating a production world and persist the result.
    It is never called automatically or as an import side effect.

    Returns:
        An integer satisfying ``0 <= seed < 2**128``.
    """
    return randbits(128)


def counter_rng(
    world_seed: int,
    entity_id: EntityId,
    tick: Tick,
    seq: int,
    /,
) -> Rng:
    """Create the deterministic stream for one action occurrence.

    Args:
        world_seed: Persisted unsigned 128-bit seed for the world.
        entity_id: Owner of the action.
        tick: Logical tick being processed.
        seq: Permanent sequence number assigned when the action was admitted.

    Returns:
        A fresh stream positioned before its first draw.

    Raises:
        TypeError: If `world_seed` is not an integer or is a boolean.
        ValueError: If `world_seed` is outside the unsigned 128-bit range.
    """
    _validate_world_seed(world_seed)
    stream_seed = _stream_seed(world_seed, entity_id, tick, seq)
    return _CounterRng(stream_seed)


def event_resolution_rng(world_seed: int, event_id: str, tick: Tick, /) -> Rng:
    """Create one internal stream for shared Event resolution.

    This is intentionally not re-exported. Applications receive the resulting stream
    through `ResolutionContext`; they never construct or seed it themselves.
    Its separate BLAKE2 personalization makes collision with an action stream
    impossible even if an application chooses matching textual identities.
    """
    _validate_world_seed(world_seed)
    digest = blake2b(
        digest_size=8,
        key=world_seed.to_bytes(16, "big"),
        person=_EVENT_PERSONALIZATION,
    )
    parts = (event_id.encode("utf-8"), str(tick).encode("ascii"))
    for part in parts:
        digest.update(len(part).to_bytes(8, "big"))
        digest.update(part)
    return _CounterRng(int.from_bytes(digest.digest(), "big"))


class _CounterRng:
    """A private SplitMix64 stream rooted in one stable action identity."""

    __slots__ = ("_draw_index", "_stream_seed")

    def __init__(self, stream_seed: int) -> None:
        self._stream_seed = stream_seed
        self._draw_index = 0

    def random(self) -> float:
        """Uniform in `[0.0, 1.0)` using Python float's 53-bit precision."""
        return (self._next_uint64() >> 11) * _FLOAT_UNIT

    def below(self, n: int, /) -> int:
        """Uniform integer in `[0, n)` without modulo bias."""
        _validate_upper_bound(n)
        acceptance_limit = _UINT64_SIZE - (_UINT64_SIZE % n)

        while True:
            value = self._next_uint64()
            if value < acceptance_limit:
                return value % n

    def _next_uint64(self) -> int:
        if self._draw_index >= _UINT64_SIZE:
            raise OverflowError("an RNG stream cannot contain more than 2**64 draws")

        value = (self._stream_seed + (self._draw_index + 1) * _SPLITMIX_INCREMENT) & _UINT64_MASK
        self._draw_index += 1
        return _mix_uint64(value)


def _validate_world_seed(world_seed: object) -> None:
    if isinstance(world_seed, bool) or not isinstance(world_seed, int):
        raise TypeError("world_seed must be an int, not bool")
    if not 0 <= world_seed < _UINT128_SIZE:
        raise ValueError("world_seed must satisfy 0 <= world_seed < 2**128")


def _validate_upper_bound(n: object) -> None:
    if isinstance(n, bool) or not isinstance(n, int):
        raise TypeError("n must be an int, not bool")
    if not 1 <= n <= _UINT64_SIZE:
        raise ValueError("n must satisfy 1 <= n <= 2**64")


def _stream_seed(world_seed: int, entity_id: EntityId, tick: Tick, seq: int) -> int:
    digest = blake2b(
        digest_size=8,
        key=world_seed.to_bytes(16, "big"),
        person=_PERSONALIZATION,
    )
    parts = (
        entity_id.encode("utf-8"),
        str(tick).encode("ascii"),
        str(seq).encode("ascii"),
    )
    for part in parts:
        digest.update(len(part).to_bytes(8, "big"))
        digest.update(part)
    return int.from_bytes(digest.digest(), "big")


def _mix_uint64(value: int) -> int:
    value = ((value ^ (value >> 30)) * _SPLITMIX_MULTIPLIER_1) & _UINT64_MASK
    value = ((value ^ (value >> 27)) * _SPLITMIX_MULTIPLIER_2) & _UINT64_MASK
    return value ^ (value >> 31)
