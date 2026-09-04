"""Run Tinyworld and print a compact deterministic summary."""

import sys
from collections.abc import Sequence

from examples.tinyworld import run_tinyworld


def main(argv: Sequence[str] | None = None) -> None:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) > 2:
        raise SystemExit("usage: python -m examples.tinyworld [ticks] [seed]")

    ticks = int(arguments[0]) if arguments else 1_000_000
    seed = int(arguments[1]) if len(arguments) == 2 else 20_260_904
    result = run_tinyworld(ticks=ticks, seed=seed)

    print(f"ticks: {result.ticks}")
    print(f"seed: {result.seed}")
    print(f"Lira's final HP: {result.final_hp}")
    print(f"journal entries: {len(result.journal)}")
    print(f"potions drunk: {result.potions_drunk}")
    print(f"journal SHA-256: {result.journal_hash}")
    print("\nFirst ten journal entries:")
    for tick, line in result.journal[:10]:
        print(f"  [{tick}] {line}")
    print("\nLast ten journal entries:")
    for tick, line in result.journal[-10:]:
        print(f"  [{tick}] {line}")


if __name__ == "__main__":
    main()
