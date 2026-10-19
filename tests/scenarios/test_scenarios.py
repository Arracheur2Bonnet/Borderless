"""Scripted task-completion suite as pytest: one test per mission in scenarios/tasks.py.

Uses the same runner and evaluation as scripts/run_scenarios.py, so a green test == a completed task.
"""
import pytest

from scenarios.engine import evaluate, run_task
from scenarios.tasks import TASKS


@pytest.mark.parametrize("task", TASKS, ids=[t["id"] for t in TASKS])
def test_scripted_task(task, monkeypatch):
    monkeypatch.delenv("BG_PARSER", raising=False)   # offline, deterministic rules parser
    failures = evaluate(task, run_task(task))
    assert not failures, f"{task['id']} not completed:\n  - " + "\n  - ".join(failures)


def test_suite_shape():
    assert len(TASKS) == 21
    for t in TASKS:
        assert t["criteria"].get("max_user_turns"), f"{t['id']} has no turn budget"
        assert sum(1 for s in t["steps"] if "say" in s) <= t["criteria"]["max_user_turns"]
