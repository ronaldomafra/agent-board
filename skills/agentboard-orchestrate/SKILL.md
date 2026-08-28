---
name: agentboard-orchestrate
description: Coordinate Codex development agents through an AgentBoard project. Use when drafting or approving plans, scheduling and claiming tasks, spawning native Codex agents, reporting checkpoints, reviewing evidence, integrating approved local Git work, or resolving blocked work.
metadata:
  short-description: Coordinate AgentBoard tasks
---

# AgentBoard Orchestration

AgentBoard is the authority for plans, task phases, reservations, leases, WIP, reviews and
evidence. Codex remains the authority that creates and communicates with subagents. Never
simulate a claim in chat or mark work complete without the corresponding AgentBoard command.

## Open and plan

1. Read `AGENTS.md`, the active `agentboard.yaml` and the relevant profile in
   `.codex/agents/`. Then read only the project documents explicitly referenced by that
   project's `AGENTS.md`. `DEVELOPMENT_GUIDE.md` and `docs/ARCHITECTURE.md` are optional
   project documents, not prerequisites for every AgentBoard consumer.
2. Call `project_open`, then `board_snapshot`, `plan_get` and `config_get`.
3. If requirements are not represented by an approved revision, use
   `plan_draft_create` or `plan_revision_create`, update and validate the draft, present its
   impact to the user, and wait for the required approval before calling `plan_approve` or
   `plan_revision_approve`.
4. Never start tasks from an unapproved plan.

## Schedule and spawn

1. Call `schedule_next`. Respect the returned deterministic order and every ineligibility reason.
2. Choose a registered agent whose profile matches the task, then call `task_claim` with the
   task ID, agent ID, latest task version and a unique idempotency key. The service derives the
   profile and exclusive path lease from the approved task; never send or widen paths in the
   claim. A claim is a reservation, not a started run.
3. Spawn the worker with Codex's native agent tool. Give it the AgentBoard task ID, base SHA,
   explicit file lease, acceptance criteria, tests and the short-lived run capability returned
   by the claim. Deliver that capability only to the assigned worker. State that other agents
   share the tree and that the worker must not revert their changes.
4. After spawn succeeds, call `run_start` with the reservation, native thread/agent identifier,
   latest version and a new idempotency key. If spawn fails, release or expire the reservation;
   call `task_release_reservation` immediately and do not invent a running agent.
5. Do not spawn overlapping writers. Reservations, active runs and verification all consume WIP.

## Run and checkpoint

- Send `task_heartbeat` at meaningful checkpoints using the run capability and current lease
  generation. A heartbeat continues the existing policy revision; it is not a new claim.
- Record objective checkpoints, changed files, tests and concise evidence. Do not manufacture a
  canonical percentage.
- Use `git_checkpoint` only for a local checkpoint owned by the run and only for leased paths.
- On an actionable impediment, call `task_block` with the concrete reason, owner and preserved
  checkpoint. Blocking ends the run and releases WIP/file leases.
- Use `task_report_result` to submit evidence for verification. Use `run_fail` for a failed
  attempt. A retry is a new claim and is automatic only for configured transient failures.

## Review and integrate

1. Select an allowed reviewer by risk: low/medium may use the orchestrator; high requires an
   independent reviewer; critical also requires explicit human approval.
2. Call `review_claim`, spawn the reviewer natively, then call `review_start`. A worker may not
   review its own work.
3. The reviewer calls `review_decide` with acceptance evidence. `CHANGES_REQUESTED` returns the
   task to backlog for a new claim and preserves prior evidence.
4. If policy requires Git, call `git_integrate` only after approval. Integration is local and
   must verify the expected target SHA, a clean target and the run's file lease.
5. Final plan integration additionally requires the approved target branch and the exact reviewed
   plan-branch source SHA; reload and review again if either reference moved.
6. AgentBoard never pushes, pulls, fetches, clones, opens PRs, deploys, auto-stashes or performs
   destructive reset. Do not bypass the MCP Git adapter with a shell command.

## Finish

Return the task ID, outcome, changed files, tests, acceptance evidence, risks and next task
recommendation. `DONE` requires recorded evidence, an approved review and local integration when
the evidence profile requires it. Treat the bundled hook as defense in depth only; the domain
service, capabilities and local Git adapter remain authoritative.
