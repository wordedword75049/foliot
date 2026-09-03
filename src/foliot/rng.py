"""Addressable deterministic randomness (§9).

A global stream would couple unrelated entities through a hidden cursor: with
one shared generator, whether Ivan's blow lands depends on whether Petra's
fight -- a thousand miles away -- was processed first (§9.2). That coupling
appears in no table, survives no restart, and forbids reordering the queue.
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


class Rng(Protocol):
    """A stream already bound to `(world_seed, entity_id, tick, seq)`.

    Handlers never seed anything and never import `random`. Each draw advances
    only a local counter, so every value stays addressable: "Ivan's third roll
    in tick 5000" can be recomputed a year later without replaying anything.

    Implementations must hash stably -- never `hash()` on a `str`, which
    `PYTHONHASHSEED` salts per process and which therefore breaks replay across
    a restart in a way that looks like a rare, mysterious bug (§9.4).
    """

    def random(self) -> float:
        """Uniform in [0.0, 1.0)."""
        ...

    def below(self, n: int, /) -> int:
        """Uniform integer in `[0, n)` for `1 <= n <= 2**64`."""
        ...


def new_world_seed() -> int:
    """Return a securely generated unsigned 128-bit world seed.

    The game calls this explicitly when creating a world and persists the
    result. It is never called automatically or as an import side effect.
    """
    return randbits(128)


def counter_rng(
    world_seed: int,
    entity_id: EntityId,
    tick: Tick,
    seq: int,
    /,
) -> Rng:
    """Create the deterministic stream for one action occurrence."""
    _validate_world_seed(world_seed)
    stream_seed = _stream_seed(world_seed, entity_id, tick, seq)
    return _CounterRng(stream_seed)


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
