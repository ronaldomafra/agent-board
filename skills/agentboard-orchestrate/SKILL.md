---
name: agentboard-orchestrate
description: Coordinate one or more Codex development agents through an AgentBoard project. Use when planning tasks, claiming work, reporting progress, reviewing evidence, resolving blocked work, or updating task execution state.
metadata:
  short-description: Coordinate AgentBoard tasks
---

# AgentBoard Orchestration

## Start

1. Read `AGENTS.md`, `DEVELOPMENT_GUIDE.md` and the project orchestration configuration.
2. Call `project_open` and inspect the board snapshot before delegating work.
3. Select only tasks whose dependencies are resolved and whose lease can be acquired within WIP.

## Delegate safely

For each worker, provide task ID, base SHA, exclusive file lease, acceptance criteria and required tests. Use one worker per task. Keep overlapping writes out of domain, service and migration files.

## Operational rules

- Claim work through `task_claim`; do not infer ownership from a chat message.
- Workers send `task_heartbeat` at meaningful checkpoints and report only concise progress.
- Move work with `task_transition` using the latest version and a reason.
- Record commits, tests, acceptance and risks with `task_report_result` before verification.
- A reviewer decides whether verification becomes `DONE` or `REWORK`.
- Treat `BLOCKED` as an explicit event with a concrete blocker and next owner.

## Finish

Return the task ID, outcome, changed files, tests, evidence, risks and next task recommendation. Do not mark a task done without evidence required by project policy.

