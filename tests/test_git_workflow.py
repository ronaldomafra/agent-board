from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agentboard.domain import Actor, PolicyViolationError, ReviewStatus
from agentboard.git_local import LocalGitAdapter
from agentboard.service import BoardService

HUMAN = Actor("human", frozenset({"human"}))
ORCHESTRATOR = Actor("orchestrator", frozenset({"orchestrator"}))
WORKER = Actor("worker-1", frozenset({"worker"}))


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()


def test_git_required_flow_uses_worktree_checkpoint_and_local_integration(
    tmp_path: Path,
) -> None:
    git(tmp_path, "init")
    git(tmp_path, "checkout", "-b", "main")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "config", "user.email", "test@localhost")
    (tmp_path / ".gitignore").write_text(".agentboard/\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    git(tmp_path, "add", ".gitignore", "src/app.py")
    git(tmp_path, "commit", "-m", "base")
    base_sha = git(tmp_path, "rev-parse", "HEAD")

    service = BoardService(tmp_path / ".agentboard" / "state.db")
    content = {
        "target_branch": "main",
        "tasks": [
            {
                "id": "AB-GIT",
                "title": "Git task",
                "objective": "Change the local source",
                "acceptance": ["local value is updated"],
                "tests": ["inspect file"],
                "priority": "P1",
                "risk": "MEDIUM",
                    "suggested_profile": "worker",
                    "evidence_profile": "code_with_git",
                    "paths": ["src/app.py"],
                    "dependencies": [],
            }
        ]
    }
    service.create_plan_draft(
        "PLAN-GIT",
        "Git plan",
        content,
        expected_version=0,
        actor=HUMAN,
        idempotency_key="git-draft",
    )
    service.approve_plan(
        "PLAN-GIT",
        1,
        expected_version=0,
        actor=HUMAN,
        idempotency_key="git-approve",
    )
    service.register_agent(
        "worker-1",
        "worker",
        1,
        expected_version=0,
        actor=ORCHESTRATOR,
        idempotency_key="git-agent",
    )
    claim = service.claim_task(
        "AB-GIT",
        "worker-1",
        expected_version=0,
        actor=ORCHESTRATOR,
        idempotency_key="git-claim",
    )
    run = service.run_start(
        "AB-GIT",
        claim.data["assignment_id"],
        claim.data["lease_generation"],
        expected_version=claim.version,
        actor=WORKER,
        idempotency_key="git-run",
        thread_id="thread-worker",
    )
    worktree = Path(run.data["worktree_path"])
    (worktree / "src" / "app.py").write_text("value = 2\n", encoding="utf-8")
    checkpoint = service.git_checkpoint(
        "AB-GIT",
        run.data["run_id"],
        claim.data["lease_generation"],
        run.data["start_sha"],
        "checkpoint AB-GIT",
        expected_version=run.version,
        actor=WORKER,
        idempotency_key="git-checkpoint",
    )
    result = service.task_report_result(
        "AB-GIT",
        run.data["run_id"],
        claim.data["lease_generation"],
        [{"kind": "tests", "summary": "local acceptance passed"}],
        expected_version=checkpoint.version,
        actor=WORKER,
        idempotency_key="git-result",
    )
    claimed_review = service.review_claim(
        "AB-GIT",
        result.data["review_id"],
        ORCHESTRATOR.id,
        expected_version=result.version,
        actor=ORCHESTRATOR,
        idempotency_key="git-review-claim",
    )
    started_review = service.review_start(
        "AB-GIT",
        result.data["review_id"],
        expected_version=claimed_review.version,
        actor=ORCHESTRATOR,
        idempotency_key="git-review-start",
        thread_id="thread-reviewer",
    )
    review = service.review_decide(
        "AB-GIT",
        result.data["review_id"],
        ReviewStatus.APPROVED,
        "accepted",
        expected_version=started_review.version,
        actor=ORCHESTRATOR,
        idempotency_key="git-review",
    )
    assert review.state == "VERIFYING"

    integrated = service.git_integrate(
        "AB-GIT",
        run.data["run_id"],
        base_sha,
        expected_version=review.version,
        actor=ORCHESTRATOR,
        idempotency_key="git-integrate-task",
    )
    assert integrated.state == "DONE"
    kinds = {item["kind"] for item in service.run_get(run.data["run_id"])["evidence"]}
    assert {"git_checkpoint", "git_integration", "tests"} <= kinds
    plan_source_sha = git(
        tmp_path,
        "rev-parse",
        "refs/heads/agentboard/plan/plan-git",
    )
    with pytest.raises(PolicyViolationError, match="approved plan"):
        service.git_integrate_plan(
            "PLAN-GIT",
            1,
            "release",
            base_sha,
            plan_source_sha,
            expected_version=1,
            actor=HUMAN,
            idempotency_key="git-integrate-wrong-target",
        )
    with pytest.raises(PolicyViolationError, match="Integration source moved"):
        service.git_integrate_plan(
            "PLAN-GIT",
            1,
            "main",
            base_sha,
            base_sha,
            expected_version=1,
            actor=HUMAN,
            idempotency_key="git-integrate-stale-source",
        )

    completed = service.git_integrate_plan(
        "PLAN-GIT",
        1,
        "main",
        base_sha,
        plan_source_sha,
        expected_version=1,
        actor=HUMAN,
        idempotency_key="git-integrate-plan",
    )
    assert completed.state == "COMPLETED"
    assert (tmp_path / "src" / "app.py").read_text(encoding="utf-8") == "value = 2\n"


def test_checkpoint_effect_is_recovered_after_simulated_process_crash(
    tmp_path: Path,
) -> None:
    git(tmp_path, "init")
    git(tmp_path, "checkout", "-b", "main")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "config", "user.email", "test@localhost")
    (tmp_path / ".gitignore").write_text(".agentboard/\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    git(tmp_path, "add", ".gitignore", "src/app.py")
    git(tmp_path, "commit", "-m", "base")

    service = BoardService(tmp_path / ".agentboard" / "state.db")
    content = {
        "tasks": [
            {
                "id": "AB-CRASH",
                "title": "Crash recovery",
                "objective": "Recover an unrecorded local checkpoint",
                "acceptance": ["recovery is visible"],
                "tests": ["inspect recovery state"],
                "priority": "P1",
                "risk": "MEDIUM",
                "suggested_profile": "worker",
                "evidence_profile": "code_with_git",
                "paths": ["src/app.py"],
                "dependencies": [],
            }
        ]
    }
    service.create_plan_draft(
        "PLAN-CRASH",
        "Crash plan",
        content,
        expected_version=0,
        actor=HUMAN,
        idempotency_key="crash-draft",
    )
    service.approve_plan(
        "PLAN-CRASH",
        1,
        expected_version=0,
        actor=HUMAN,
        idempotency_key="crash-approve",
    )
    service.register_agent(
        "worker-1",
        "worker",
        1,
        expected_version=0,
        actor=ORCHESTRATOR,
        idempotency_key="crash-agent",
    )
    claim = service.claim_task(
        "AB-CRASH",
        "worker-1",
        expected_version=0,
        actor=ORCHESTRATOR,
        idempotency_key="crash-claim",
    )
    run = service.run_start(
        "AB-CRASH",
        claim.data["assignment_id"],
        claim.data["lease_generation"],
        expected_version=claim.version,
        actor=WORKER,
        idempotency_key="crash-run",
    )
    worktree = Path(run.data["worktree_path"])
    (worktree / "src" / "app.py").write_text("value = 9\n", encoding="utf-8")
    service._write_external_intent(
        "git_checkpoint",
        "crash-before-sqlite-commit",
        {
            "task_id": "AB-CRASH",
            "run_id": run.data["run_id"],
            "worktree_path": str(worktree),
            "expected_sha": run.data["start_sha"],
        },
    )
    LocalGitAdapter(tmp_path).checkpoint(
        worktree=worktree,
        expected_head=run.data["start_sha"],
        allowed_paths=("src/app.py",),
        message="effect before simulated crash",
        actor="worker-1",
    )

    recovered = BoardService(tmp_path / ".agentboard" / "state.db")

    assert recovered.task_get("AB-CRASH").display_state == "BLOCKED"
    assert recovered.run_get(run.data["run_id"])["status"] == "STALE"
    assert not list(
        (tmp_path / ".agentboard" / "external-intents").glob("*.json")
    )
