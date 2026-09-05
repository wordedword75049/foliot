"""Run Eventworld and print its deterministic story."""

import sys
from collections.abc import Sequence

from examples.eventworld import run_eventworld


def main(argv: Sequence[str] | None = None) -> None:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) > 2:
        raise SystemExit("usage: python -m examples.eventworld [ticks] [seed]")

    ticks = int(arguments[0]) if arguments else 20
    seed = int(arguments[1]) if len(arguments) == 2 else 20_260_905
    result = run_eventworld(ticks=ticks, seed=seed)

    print(f"ticks: {result.ticks}")
    print(f"seed: {result.seed}")
    print(f"Lira's final HP: {result.lira_hp}")
    print(f"arrived: {result.arrived}")
    print(f"fights: {result.fights_started} started, {result.fights_ended} ended")
    print(f"fight result: {result.fight_result}")
    print(f"journal SHA-256: {result.journal_hash}")
    print("\nJournal:")
    for tick, line in result.journal:
        print(f"  [{tick}] {line}")


if __name__ == "__main__":
    main()
