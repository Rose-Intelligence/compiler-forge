"""What a round is allowed to publish about a held-out package.

A hidden family exists to measure whether an artifact transferred technique or
memorised a target. That signal is spent the moment a miner can read the hidden
package, and a patch is the most direct disclosure there is: it carries the file
paths, the surrounding source as diff context, and the shape of the inefficiency.

These pin the boundary. They are cheap, and they guard a property that is
invisible until it has already leaked.
"""

from __future__ import annotations

from compilerforge.validator.round import RoundResult


class _Task:
    """The two fields the publication filter reads."""

    def __init__(self, task_id: str, hidden: bool) -> None:
        self.task = type("T", (), {"task_id": task_id})()
        self.hidden = hidden


class _Plan:
    def __init__(self, tasks) -> None:
        self.tasks = tasks


def _publish(best_by_task, plan, voided=frozenset()):
    """Reproduce the filter the round applies before publishing patches."""
    hidden = {t.task.task_id for t in plan.tasks if t.hidden}
    return {
        task_id: patch
        for task_id, (_score, patch) in best_by_task.items()
        if task_id not in voided and task_id not in hidden
    }


def test_a_hidden_task_patch_is_never_published():
    plan = _Plan([_Task("public-1", False), _Task("hidden-1", True)])
    best = {
        "public-1": (0.9, "--- a/src/public.c\n+++ b/src/public.c\n"),
        "hidden-1": (0.9, "--- a/src/secret.c\n+++ b/src/secret.c\n"),
    }
    published = _publish(best, plan)
    assert "public-1" in published, "public patches are the point of the bundle"
    assert "hidden-1" not in published, (
        "publishing a held-out package's patch discloses its paths and source"
    )
    assert "secret.c" not in "".join(published.values())


def test_hidden_tasks_are_still_scored():
    """Withholding the patch must not mean withholding the score.

    A hidden task that stopped counting would remove the generalisation signal
    entirely, which is the opposite of what the exclusion is for.
    """
    plan = _Plan([_Task("hidden-1", True)])
    result = RoundResult(round_number=1, plan=plan)
    # The filter touches accepted_patches only; score_artifacts is untouched.
    assert result.score_artifacts == []
    published = _publish({"hidden-1": (0.9, "patch")}, plan)
    assert published == {}


def test_voided_and_hidden_are_independent_reasons():
    plan = _Plan([_Task("a", False), _Task("b", True), _Task("c", False)])
    best = {"a": (0.5, "pa"), "b": (0.5, "pb"), "c": (0.5, "pc")}
    published = _publish(best, plan, voided={"c"})
    assert set(published) == {"a"}, "a task may be withheld for either reason"


def test_the_real_filter_excludes_hidden_tasks():
    """Guard the production source, not just this file's copy of the rule."""
    import inspect

    from compilerforge.validator import round as round_mod

    source = inspect.getsource(round_mod.RoundRunner.verify)
    assert "hidden_task_ids" in source, "the publication filter lost its hidden check"
    assert "t.hidden" in source
