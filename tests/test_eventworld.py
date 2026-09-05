import subprocess
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _run_eventworld() -> str:
    command = [sys.executable, "-m", "examples.eventworld"]
    return subprocess.check_output(command, cwd=_PROJECT_ROOT, text=True)


def test_eventworld_should_complete_one_fight_then_resume_the_walk() -> None:
    output = _run_eventworld()

    assert "fights: 1 started, 1 ended" in output
    assert "fight result: the wolf died" in output
    assert "arrived: True" in output
    assert "The haunted forest sends the ash wolf against Lira." in output
    assert "The ash wolf falls, and the path is clear." in output
    assert "[16] Lira arrives at the moonlit clearing." in output


def test_eventworld_should_replay_the_same_story_from_the_same_seed() -> None:
    first = _run_eventworld()
    second = _run_eventworld()

    assert first == second
    assert "fights: 1 started, 1 ended" in first
    assert "arrived: True" in first
    assert (
        "journal SHA-256: eb43d26dc88ffbaa342b731a1e74623ce42db9b11fbf23f4a376a975c4fb11c3" in first
    )
