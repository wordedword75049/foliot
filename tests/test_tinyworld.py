import subprocess
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_arrival_should_schedule_an_action_against_the_new_environment() -> None:
    command = [sys.executable, "-m", "examples.tinyworld", "22"]

    output = subprocess.check_output(command, cwd=_PROJECT_ROOT, text=True)

    assert "[20] Lira arrives at the moonlit clearing." in output
    assert "[21] Lira rests in the moonlit clearing." in output


def test_tinyworld_should_replay_the_same_story_from_the_same_seed() -> None:
    command = [sys.executable, "-m", "examples.tinyworld", "5000", "12345"]

    first = subprocess.check_output(command, cwd=_PROJECT_ROOT, text=True)
    second = subprocess.check_output(command, cwd=_PROJECT_ROOT, text=True)

    assert first == second
    assert "ticks: 5000" in first
    assert "seed: 12345" in first
    assert "potions drunk:" in first
    assert "potions drunk: 0" not in first
    assert "journal SHA-256:" in first
