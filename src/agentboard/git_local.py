from __future__ import annotations

import fnmatch
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final


class GitPolicyError(RuntimeError):
    """Raised before a Git operation that is outside AgentBoard's local-only policy."""


class GitConflictError(GitPolicyError):
    """Raised when a local integration cannot be completed without conflict."""


_SHA_RE: Final = re.compile(r"^[0-9a-f]{40,64}$")
_SAFE_TOKEN_RE: Final = re.compile(r"^[A-Za-z0-9._/-]+$")
_FORBIDDEN_CONFIG_RE: Final = re.compile(
    r"^(include(if)?\.|filter\.|diff\..*\.(command|textconv)$|merge\..*\.driver$)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class GitCheckpoint:
    commit_sha: str
    parent_sha: str
    paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GitIntegration:
    commit_sha: str
    target_before: str
    source_sha: str
    target_ref: str


class LocalGitAdapter:
    """Narrow Git adapter with no code path for remote operations."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.runtime_dir = self.project_root / ".agentboard"
        self.empty_hooks = self.runtime_dir / "disabled-git-hooks"
        self.empty_hooks.mkdir(parents=True, exist_ok=True)
        self.line_ending_config = self._read_safe_line_ending_config()

    def _command(self, *args: str) -> list[str]:
        return [
            "git",
            "-c",
            f"core.hooksPath={self.empty_hooks}",
            "-c",
            "commit.gpgSign=false",
            "-c",
            "tag.gpgSign=false",
            "-c",
            "protocol.file.allow=never",
            *self.line_ending_config,
            *args,
        ]

    def _read_safe_line_ending_config(self) -> tuple[str, ...]:
        """Snapshot only inert line-ending settings before isolating global config."""
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.upper().startswith("GIT_")
        }
        env.update({"GIT_TERMINAL_PROMPT": "0"})
        allowed = {
            "core.autocrlf": {"true", "false", "input"},
            "core.eol": {"native", "lf", "crlf"},
        }
        arguments: list[str] = []
        for key, values in allowed.items():
            completed = subprocess.run(
                ["git", "config", "--get", key],
                cwd=self.project_root,
                env=env,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=False,
                shell=False,
            )
            value = completed.stdout.strip().casefold()
            if completed.returncode == 0 and value in values:
                arguments.extend(("-c", f"{key}={value}"))
        return tuple(arguments)

    def _run(
        self,
        *args: str,
        cwd: Path | None = None,
        check: bool = True,
        input_text: str | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.upper().startswith("GIT_")
        }
        env.update({
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        })
        if extra_env:
            env.update(extra_env)
        completed = subprocess.run(
            self._command(*args),
            cwd=cwd or self.project_root,
            env=env,
            input=input_text,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
            shell=False,
        )
        if check and completed.returncode:
            detail = (completed.stderr or completed.stdout).strip()
            raise GitPolicyError(detail or f"local Git command failed: {args[0]}")
        return completed

    def assert_repository_safe(self) -> None:
        inside = self._run("rev-parse", "--is-inside-work-tree").stdout.strip()
        if inside != "true":
            raise GitPolicyError("Project is not a Git worktree")

        configured = self._run(
            "config",
            "--local",
            "--show-origin",
            "--name-only",
            "--get-regexp",
            ".*",
            check=False,
        )
        for entry in configured.stdout.splitlines():
            key = entry.rsplit(None, 1)[-1].strip()
            if _FORBIDDEN_CONFIG_RE.match(key):
                raise GitPolicyError(f"External Git driver/filter is not allowed: {key}")

        self._assert_attributes_safe(self.project_root)
        info_attributes = self.common_directory() / "info" / "attributes"
        self._assert_attribute_file_safe(info_attributes)

        modules = self.project_root / ".gitmodules"
        if modules.is_file() and modules.read_text(encoding="utf-8", errors="replace").strip():
            raise GitPolicyError("Git submodules are outside AgentBoard v1 policy")

    def common_directory(self) -> Path:
        value = self._run(
            "rev-parse", "--path-format=absolute", "--git-common-dir"
        ).stdout.strip()
        return Path(value).resolve()

    def head_sha(self, cwd: Path | None = None) -> str:
        value = self._run("rev-parse", "HEAD", cwd=cwd).stdout.strip()
        return self._validate_sha(value)

    def resolve_ref(self, ref: str) -> str:
        self._validate_ref(ref)
        value = self._run("rev-parse", "--verify", f"{ref}^{{commit}}").stdout.strip()
        return self._validate_sha(value)

    def ensure_plan_branch(self, plan_id: str, base_sha: str) -> str:
        self.assert_repository_safe()
        self._validate_sha(base_sha)
        branch = f"agentboard/plan/{self._slug(plan_id)}"
        self._validate_ref(branch)
        ref = f"refs/heads/{branch}"
        existing = self._run("show-ref", "--verify", "--hash", ref, check=False)
        if existing.returncode == 0:
            return branch
        self._run("update-ref", ref, base_sha, "0" * len(base_sha))
        return branch

    def create_run_worktree(
        self,
        *,
        plan_id: str,
        run_id: str,
        start_sha: str,
    ) -> tuple[Path, str]:
        self.assert_repository_safe()
        self._validate_sha(start_sha)
        worktree, branch = self.run_worktree_spec(plan_id=plan_id, run_id=run_id)
        worktrees_root = (self.runtime_dir / "worktrees").resolve()
        if worktree.exists():
            raise GitPolicyError(f"Run worktree already exists: {worktree}")
        worktrees_root.mkdir(parents=True, exist_ok=True)
        self._run("worktree", "add", "-b", branch, str(worktree), start_sha)
        return worktree, branch

    def run_worktree_spec(self, *, plan_id: str, run_id: str) -> tuple[Path, str]:
        branch = f"agentboard/run/{self._slug(plan_id)}/{self._slug(run_id)}"
        self._validate_ref(branch)
        worktrees_root = (self.runtime_dir / "worktrees").resolve()
        worktree = (worktrees_root / self._slug(run_id)).resolve()
        if worktrees_root not in worktree.parents:
            raise GitPolicyError("Run worktree escaped the project runtime directory")
        return worktree, branch

    def cleanup_owned_worktree(
        self,
        *,
        worktree: Path,
        branch: str,
        expected_start_sha: str,
    ) -> None:
        """Remove only an orphaned AgentBoard worktree and its unchanged run ref."""
        self._validate_ref(branch)
        self._validate_sha(expected_start_sha)
        owned_root = (self.runtime_dir / "worktrees").resolve()
        target = worktree.resolve()
        if owned_root not in target.parents:
            raise GitPolicyError("Refusing to clean a worktree outside AgentBoard runtime state")
        if target.exists():
            self._run("worktree", "remove", "--force", str(target))
        ref = f"refs/heads/{branch}"
        current = self._run("show-ref", "--verify", "--hash", ref, check=False)
        if current.returncode == 0:
            current_sha = self._validate_sha(current.stdout.strip())
            if current_sha == expected_start_sha:
                self._run("update-ref", "-d", ref, current_sha)

    def checkpoint(
        self,
        *,
        worktree: Path,
        expected_head: str,
        allowed_paths: tuple[str, ...],
        message: str,
        actor: str,
    ) -> GitCheckpoint:
        self.assert_repository_safe()
        self._assert_attributes_safe(worktree)
        self._validate_sha(expected_head)
        if self.head_sha(worktree) != expected_head:
            raise GitPolicyError("Run branch moved; reload before checkpointing")
        changed = self._changed_paths(worktree)
        if not changed:
            raise GitPolicyError("There are no changes to checkpoint")
        outside = [path for path in changed if not self._path_is_allowed(path, allowed_paths)]
        if outside:
            raise GitPolicyError(f"Changed paths are outside the task lease: {', '.join(outside)}")

        self._run("add", "--all", "--", *changed, cwd=worktree)
        staged = tuple(
            sorted(
                self._nul_values(
                    self._run(
                        "diff",
                        "--no-ext-diff",
                        "--cached",
                        "--name-only",
                        "-z",
                        cwd=worktree,
                    ).stdout
                )
            )
        )
        if staged != tuple(sorted(changed)):
            raise GitPolicyError("Staged paths differ from the validated task changes")

        author = actor.strip()[:80] or "AgentBoard"
        env = {
            "GIT_AUTHOR_NAME": author,
            "GIT_AUTHOR_EMAIL": "agentboard@localhost",
            "GIT_COMMITTER_NAME": "AgentBoard",
            "GIT_COMMITTER_EMAIL": "agentboard@localhost",
        }
        self._run("commit", "--no-verify", "-m", message[:200], cwd=worktree, extra_env=env)
        return GitCheckpoint(
            commit_sha=self.head_sha(worktree),
            parent_sha=expected_head,
            paths=staged,
        )

    def integrate_refs(
        self,
        *,
        target_branch: str,
        source_ref: str,
        expected_target_sha: str,
        expected_source_sha: str,
        message: str,
        actor: str,
        require_clean_worktree: bool = True,
    ) -> GitIntegration:
        """Create and atomically advance a local merge commit without checkout or hooks."""
        self.assert_repository_safe()
        self._validate_ref(target_branch)
        self._validate_ref(source_ref)
        self._validate_sha(expected_target_sha)
        self._validate_sha(expected_source_sha)
        target_ref = f"refs/heads/{target_branch}"
        current = self.resolve_ref(target_ref)
        if current != expected_target_sha:
            raise GitPolicyError("Integration target moved; reload and review the new target")
        changed = self._changed_paths(self.project_root) if require_clean_worktree else ()
        if changed:
            raise GitPolicyError(
                "Primary worktree must be clean before final integration: "
                + ", ".join(changed)
            )
        source_sha = self.resolve_ref(source_ref)
        if source_sha != expected_source_sha:
            raise GitPolicyError(
                "Integration source moved after checkpoint/review; create and review "
                "a new AgentBoard checkpoint"
            )

        ancestor = self._run("merge-base", "--is-ancestor", source_sha, current, check=False)
        if ancestor.returncode == 0:
            return GitIntegration(
                commit_sha=current,
                target_before=current,
                source_sha=source_sha,
                target_ref=target_ref,
            )

        tree_sha = self._merged_tree(current, source_sha)
        checked_out = self._run(
            "symbolic-ref", "--quiet", "--short", "HEAD", check=False
        ).stdout.strip()
        identity = actor.strip()[:80] or "AgentBoard"
        commit_env = {
            "GIT_AUTHOR_NAME": identity,
            "GIT_AUTHOR_EMAIL": "agentboard@localhost",
            "GIT_COMMITTER_NAME": "AgentBoard",
            "GIT_COMMITTER_EMAIL": "agentboard@localhost",
        }
        if checked_out == target_branch:
            self._run(
                "merge",
                "--no-ff",
                "--no-edit",
                "--no-verify",
                "-m",
                message[:200],
                source_ref,
                extra_env=commit_env,
            )
            commit_sha = self.head_sha()
            return GitIntegration(
                commit_sha=commit_sha,
                target_before=current,
                source_sha=source_sha,
                target_ref=target_ref,
            )
        commit = self._run(
            "commit-tree",
            tree_sha,
            "-p",
            current,
            "-p",
            source_sha,
            input_text=f"{message[:200]}\n",
            extra_env=commit_env,
        ).stdout.strip()
        commit_sha = self._validate_sha(commit)
        self._run("update-ref", target_ref, commit_sha, current)
        return GitIntegration(
            commit_sha=commit_sha,
            target_before=current,
            source_sha=source_sha,
            target_ref=target_ref,
        )

    def _merged_tree(self, current: str, source_sha: str) -> str:
        help_result = self._run("merge-tree", "-h", check=False)
        help_text = f"{help_result.stdout}\n{help_result.stderr}"
        if "--write-tree" in help_text:
            merged = self._run(
                "merge-tree", "--write-tree", current, source_sha, check=False
            )
            if merged.returncode:
                detail = (merged.stdout + "\n" + merged.stderr).strip()
                raise GitConflictError(detail or "Local integration has conflicts")
            first_line = merged.stdout.splitlines()[0].strip() if merged.stdout else ""
            return self._validate_sha(first_line)
        return self._merged_tree_with_temporary_index(current, source_sha)

    def _merged_tree_with_temporary_index(
        self,
        current: str,
        source_sha: str,
    ) -> str:
        """Conservative local three-way merge for Git versions before merge-tree --write-tree."""

        bases = [
            self._validate_sha(value)
            for value in self._run("merge-base", "--all", current, source_sha)
            .stdout.strip()
            .splitlines()
            if value.strip()
        ]
        if len(bases) != 1:
            raise GitConflictError(
                "Local integration has multiple merge bases and requires manual review"
            )
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        descriptor, raw_index = tempfile.mkstemp(
            prefix="merge-index-",
            suffix=".tmp",
            dir=self.runtime_dir,
        )
        os.close(descriptor)
        index_path = Path(raw_index)
        index_path.unlink()
        index_environment = {"GIT_INDEX_FILE": str(index_path)}
        try:
            merged = self._run(
                "read-tree",
                "-m",
                bases[0],
                current,
                source_sha,
                check=False,
                extra_env=index_environment,
            )
            unmerged = self._run(
                "ls-files",
                "-u",
                check=False,
                extra_env=index_environment,
            )
            if merged.returncode or unmerged.stdout.strip():
                detail = (merged.stdout + "\n" + merged.stderr).strip()
                raise GitConflictError(detail or "Local integration has conflicts")
            tree = self._run("write-tree", extra_env=index_environment).stdout.strip()
            return self._validate_sha(tree)
        finally:
            index_path.unlink(missing_ok=True)

    def _changed_paths(self, cwd: Path) -> tuple[str, ...]:
        records = self._run(
            "status", "--porcelain=v1", "-z", "--untracked-files=all", cwd=cwd
        ).stdout.split("\0")
        values: set[str] = set()
        index = 0
        while index < len(records):
            record = records[index]
            index += 1
            if not record:
                continue
            if len(record) < 4:
                raise GitPolicyError("Git returned an invalid status record")
            status = record[:2]
            values.add(record[3:].replace("\\", "/"))
            if "R" in status or "C" in status:
                if index >= len(records):
                    raise GitPolicyError("Git returned an incomplete rename record")
                values.add(records[index].replace("\\", "/"))
                index += 1
        return tuple(
            sorted(path for path in values if not path.startswith(".agentboard/"))
        )

    def _assert_attributes_safe(self, root: Path) -> None:
        root = root.resolve()
        for path in root.rglob(".gitattributes"):
            relative = path.relative_to(root)
            if relative.parts[:1] in {(".git",), (".agentboard",)}:
                continue
            self._assert_attribute_file_safe(path)

    @staticmethod
    def _assert_attribute_file_safe(path: Path) -> None:
        if not path.is_file():
            return
        text = path.read_text(encoding="utf-8", errors="replace").lower()
        if any(token in text for token in ("filter=", "diff=", "merge=", "lfs")):
            raise GitPolicyError(
                f"External Git attributes/drivers are not allowed: {path}"
            )

    @staticmethod
    def _nul_values(value: str) -> tuple[str, ...]:
        return tuple(item.replace("\\", "/") for item in value.split("\0") if item)

    @staticmethod
    def _path_is_allowed(path: str, patterns: tuple[str, ...]) -> bool:
        normalized = PurePosixPath(path).as_posix()
        if normalized.startswith(("../", "/")):
            return False
        for raw in patterns:
            pattern = raw.replace("\\", "/").lstrip("./")
            if fnmatch.fnmatchcase(normalized, pattern) or normalized == pattern.rstrip("/"):
                return True
            prefix = pattern.rstrip("/*")
            if prefix and normalized.startswith(f"{prefix}/") and pattern.endswith("/**"):
                return True
        return False

    @staticmethod
    def _slug(value: str) -> str:
        slug = re.sub(r"[^a-z0-9._-]+", "-", value.lower()).strip("-")
        if not slug or len(slug) > 80:
            raise GitPolicyError("Invalid AgentBoard identifier for local Git ref")
        return slug

    @staticmethod
    def _validate_sha(value: str) -> str:
        if not _SHA_RE.fullmatch(value):
            raise GitPolicyError("Git did not return a valid object id")
        return value

    @staticmethod
    def _validate_ref(value: str) -> str:
        if (
            not value
            or not _SAFE_TOKEN_RE.fullmatch(value)
            or value.startswith(("-", "/", "."))
            or ".." in value
            or value.endswith(("/", ".lock"))
            or "@{" in value
        ):
            raise GitPolicyError("Unsafe local Git ref")
        return value
