import os
import subprocess
import sys

import pytest

from foliot import EntityId, counter_rng, new_world_seed


def test_new_world_seed_should_return_an_unsigned_128_bit_integer() -> None:
    seed = new_world_seed()

    assert isinstance(seed, int)
    assert not isinstance(seed, bool)
    assert 0 <= seed < 2**128


@pytest.mark.parametrize("seed", [0, 1, 2**128 - 1])
def test_counter_rng_should_accept_every_valid_seed_boundary(seed: int) -> None:
    counter_rng(seed, EntityId("ivan"), 500, 17)


@pytest.mark.parametrize("seed", [-1, 2**128])
def test_counter_rng_should_reject_seed_outside_128_bits(seed: int) -> None:
    with pytest.raises(ValueError, match="0 <= world_seed < 2\\*\\*128"):
        counter_rng(seed, EntityId("ivan"), 500, 17)


@pytest.mark.parametrize("seed", [True, False, 1.5, "1", None])
def test_counter_rng_should_reject_non_integer_seed(seed: object) -> None:
    with pytest.raises(TypeError, match="world_seed must be an int, not bool"):
        counter_rng(seed, EntityId("ivan"), 500, 17)  # pyright: ignore[reportArgumentType] -- runtime boundary test


def test_same_action_identity_should_reproduce_the_same_draws() -> None:
    first = counter_rng(12345, EntityId("ivan"), 500, 17)
    second = counter_rng(12345, EntityId("ivan"), 500, 17)

    assert [first.random() for _ in range(5)] == [second.random() for _ in range(5)]


def test_public_draws_should_match_the_v1_golden_vectors() -> None:
    floats = counter_rng(12345, EntityId("ivan"), 500, 17)
    integers = counter_rng(12345, EntityId("ivan"), 500, 17)

    assert [floats.random() for _ in range(5)] == [
        0.33787483798917195,
        0.995814939465654,
        0.6409263901021772,
        0.09420761639033637,
        0.7171525934520891,
    ]
    assert [integers.below(n) for n in (1, 2, 6, 100, 2**64)] == [
        0,
        0,
        4,
        15,
        13_229_130_353_207_762_089,
    ]


def test_each_part_of_action_identity_should_select_a_different_stream() -> None:
    identities = [
        (12345, EntityId("ivan"), 500, 17),
        (54321, EntityId("ivan"), 500, 17),
        (12345, EntityId("petra"), 500, 17),
        (12345, EntityId("ivan"), 501, 17),
        (12345, EntityId("ivan"), 500, 18),
    ]

    first_draws = {counter_rng(*identity).random() for identity in identities}

    assert len(first_draws) == len(identities)


def test_random_should_stay_inside_its_half_open_interval() -> None:
    rng = counter_rng(12345, EntityId("ivan"), 500, 17)

    values = [rng.random() for _ in range(1_000)]

    assert all(0.0 <= value < 1.0 for value in values)


@pytest.mark.parametrize("n", [1, 2, 6, 100, 2**64])
def test_below_should_stay_inside_its_half_open_interval(n: int) -> None:
    rng = counter_rng(12345, EntityId("ivan"), 500, 17)

    values = [rng.below(n) for _ in range(1_000)]

    assert all(0 <= value < n for value in values)


@pytest.mark.parametrize("n", [0, -1, 2**64 + 1])
def test_below_should_reject_bounds_outside_64_bits(n: int) -> None:
    rng = counter_rng(12345, EntityId("ivan"), 500, 17)

    with pytest.raises(ValueError, match="1 <= n <= 2\\*\\*64"):
        rng.below(n)


@pytest.mark.parametrize("n", [True, False, 1.5, "6", None])
def test_below_should_reject_non_integer_bound(n: object) -> None:
    rng = counter_rng(12345, EntityId("ivan"), 500, 17)

    with pytest.raises(TypeError, match="n must be an int, not bool"):
        rng.below(n)  # pyright: ignore[reportArgumentType] -- runtime boundary test


def test_below_one_should_return_zero_and_consume_a_draw() -> None:
    after_no_choice = counter_rng(12345, EntityId("ivan"), 500, 17)
    untouched = counter_rng(12345, EntityId("ivan"), 500, 17)

    assert after_no_choice.below(1) == 0
    after = after_no_choice.random()
    untouched.random()

    assert after == untouched.random()


def test_stream_should_ignore_python_hash_randomization_across_processes() -> None:
    program = """
from foliot import EntityId, counter_rng
rng = counter_rng(12345, EntityId(\"ivan\"), 500, 17)
print([rng.random() for _ in range(5)])
"""

    def output_with_hash_seed(hash_seed: str) -> str:
        environment = os.environ.copy()
        environment["PYTHONHASHSEED"] = hash_seed
        return subprocess.check_output(
            [sys.executable, "-c", program],
            env=environment,
            text=True,
        )

    assert output_with_hash_seed("1") == output_with_hash_seed("987654")
