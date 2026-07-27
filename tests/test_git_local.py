from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agentboard.git_local import GitPolicyError, LocalGitAdapter


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def repository(tmp_path: Path) -> Path:
    git(tmp_path, "init")
    git(tmp_path, "checkout", "-b", "main")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "config", "user.email", "test@localhost")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    git(tmp_path, "add", "src/app.py")
    git(tmp_path, "commit", "-m", "base")
    return tmp_path


def test_local_checkpoint_and_ref_integration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = repository(tmp_path)
    monkeypatch.setattr(
        LocalGitAdapter,
        "_merged_tree",
        LocalGitAdapter._merged_tree_with_temporary_index,
    )
    adapter = LocalGitAdapter(root)
    base = adapter.head_sha()
    plan_branch = adapter.ensure_plan_branch("PLAN-1", base)
    worktree, run_branch = adapter.create_run_worktree(
        plan_id="PLAN-1", run_id="RUN-1", start_sha=base
    )
    (worktree / "src" / "app.py").write_text("value = 2\n", encoding="utf-8")

    checkpoint = adapter.checkpoint(
        worktree=worktree,
        expected_head=base,
        allowed_paths=("src/**",),
        message="AgentBoard checkpoint",
        actor="worker-1",
    )
    integrated = adapter.integrate_refs(
        target_branch=plan_branch,
        source_ref=run_branch,
        expected_target_sha=base,
        expected_source_sha=checkpoint.commit_sha,
        message="Integrate RUN-1",
        actor="reviewer-1",
        require_clean_worktree=False,
    )

    assert checkpoint.paths == ("src/app.py",)
    assert integrated.target_before == base
    assert adapter.resolve_ref(plan_branch) == integrated.commit_sha


def test_integration_rejects_source_that_moved_after_checkpoint(
    tmp_path: Path,
) -> None:
    root = repository(tmp_path)
    adapter = LocalGitAdapter(root)
    base = adapter.head_sha()
    plan_branch = adapter.ensure_plan_branch("PLAN-PIN", base)
    worktree, run_branch = adapter.create_run_worktree(
        plan_id="PLAN-PIN",
        run_id="RUN-PIN",
        start_sha=base,
    )
    (worktree / "src" / "app.py").write_text("value = 2\n", encoding="utf-8")
    checkpoint = adapter.checkpoint(
        worktree=worktree,
        expected_head=base,
        allowed_paths=("src/app.py",),
        message="Reviewed checkpoint",
        actor="worker-1",
    )
    (worktree / "src" / "app.py").write_text("value = 3\n", encoding="utf-8")
    git(worktree, "add", "src/app.py")
    git(worktree, "commit", "-m", "unreviewed change")

    with pytest.raises(GitPolicyError, match="source moved"):
        adapter.integrate_refs(
            target_branch=plan_branch,
            source_ref=run_branch,
            expected_target_sha=base,
            expected_source_sha=checkpoint.commit_sha,
            message="Must not integrate",
            actor="reviewer-1",
            require_clean_worktree=False,
        )


def test_checkpoint_rejects_changes_outside_lease(tmp_path: Path) -> None:
    root = repository(tmp_path)
    adapter = LocalGitAdapter(root)
    base = adapter.head_sha()
    worktree, _ = adapter.create_run_worktree(
        plan_id="PLAN-1", run_id="RUN-2", start_sha=base
    )
    (worktree / "README.md").write_text("outside\n", encoding="utf-8")

    with pytest.raises(GitPolicyError, match="outside"):
        adapter.checkpoint(
            worktree=worktree,
            expected_head=base,
            allowed_paths=("src/**",),
            message="must fail",
            actor="worker-1",
        )


def test_repository_with_external_filter_is_rejected(tmp_path: Path) -> None:
    root = repository(tmp_path)
    git(root, "config", "filter.danger.clean", "external-command")

    with pytest.raises(GitPolicyError, match="filter"):
        LocalGitAdapter(root).assert_repository_safe()


def test_repository_with_local_config_include_is_rejected(tmp_path: Path) -> None:
    root = repository(tmp_path)
    included = tmp_path / "included.config"
    included.write_text("[filter \"danger\"]\n\tclean = external-command\n", encoding="utf-8")
    git(root, "config", "include.path", str(included))

    with pytest.raises(GitPolicyError, match="include|filter"):
        LocalGitAdapter(root).assert_repository_safe()


def test_nested_external_attributes_are_rejected(tmp_path: Path) -> None:
    root = repository(tmp_path)
    (root / "src" / ".gitattributes").write_text(
        "*.bin filter=danger\n",
        encoding="utf-8",
    )

    with pytest.raises(GitPolicyError, match="attributes"):
        LocalGitAdapter(root).assert_repository_safe()


def test_inherited_git_environment_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = repository(tmp_path)
    hostile = tmp_path / "hostile-global.config"
    hostile.write_text(
        "[filter \"danger\"]\n\tclean = external-command\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(hostile))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "filter.injected.clean")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "external-command")

    LocalGitAdapter(root).assert_repository_safe()
