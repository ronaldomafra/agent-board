export type Risk = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL" | string;

export interface Dependency {
  task_id?: string;
  id?: string;
  title?: string;
  type?: "REQUIRES" | "ORDER_AFTER" | string;
  satisfied?: boolean;
  waived?: boolean;
}

export interface Checkpoint {
  id?: string;
  title?: string;
  kind?: string;
  summary?: string;
  status?: "PENDING" | "PASSED" | "FAILED" | "RECORDED" | string;
  created_at?: string;
  payload?: Record<string, unknown>;
}

export interface Evidence {
  id?: string;
  kind?: string;
  summary?: string;
  value?: string;
  recorded_at?: string;
  created_at?: string;
  payload?: Record<string, unknown>;
}

export interface Lease {
  owner?: string;
  generation?: number;
  expires_at?: string;
  paths?: string[];
}

export interface Review {
  status?: string;
  reviewer?: string;
  reviewer_id?: string;
  summary?: string;
  decision_reason?: string;
  decided_at?: string;
}

export interface Task {
  id: string;
  title?: string;
  objective?: string;
  scope?: string | string[];
  acceptance_criteria?: string[];
  acceptance?: string[];
  tests?: string[];
  priority?: "P0" | "P1" | "P2" | "P3" | string;
  risk?: Risk;
  phase?: string;
  state?: string;
  status?: string;
  version?: number;
  profile?: string;
  suggested_profile?: string;
  agent_id?: string;
  blocked?: boolean;
  blocked_reason?: string;
  stale_reason?: string;
  paths?: string[];
  dependencies?: Dependency[];
  dependents?: Dependency[];
  checkpoints?: Checkpoint[];
  evidence?: Evidence[];
  lease?: Lease | null;
  review?: Review | null;
  run?: Run | null;
  updated_at?: string;
  reasons?: string[];
}

export interface Agent {
  id: string;
  name?: string;
  profile?: string;
  status?: string;
  health?: string;
  current_task_id?: string;
  last_heartbeat?: string;
  last_heartbeat_at?: string;
  assignment_status?: string;
}

export interface Run {
  id: string;
  task_id?: string;
  task_title?: string;
  agent_id?: string;
  status?: string;
  attempt?: number;
  started_at?: string;
  finished_at?: string;
  latest_checkpoint?: string;
  failure_reason?: string;
  input_tokens?: number | null;
  output_tokens?: number | null;
  total_tokens?: number | null;
  completed_at?: string | null;
  duration_seconds?: number | null;
}

export interface UsageTotals {
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  completed_duration_seconds: number;
  reported_runs: number;
  completed_runs: number;
}

export interface Plan {
  id: string;
  name?: string;
  title?: string;
  revision: number;
  status?: string;
  version?: number;
  parent_revision?: number | null;
  approved_at?: string | null;
  created_at?: string;
}

export interface PlanContent {
  objective?: string;
  target_branch?: string | null;
  impact_reason?: string;
  tasks?: Task[];
  dependencies?: Array<{
    upstream_task_id?: string;
    downstream_task_id?: string;
    task_id?: string;
    kind?: string;
    type?: string;
  }>;
  [key: string]: unknown;
}

export interface PlanRevision extends Plan {
  content: PlanContent;
  content_json?: string;
}

export interface PlanValidation {
  valid: boolean;
  plan_id: string;
  revision: number;
  task_count: number;
  graph_metrics?: Record<string, [number, number]>;
  impact?: {
    parent_revision?: number;
    added?: string[];
    removed?: string[];
    changed?: string[];
    active_runs_preserved?: boolean;
    requires_human_approval?: boolean;
    human_sensitive_tasks?: string[];
  } | null;
}

export interface ConfigRevision {
  id: string;
  revision: number;
  status: string;
  version: number;
  content_hash: string;
  content: Record<string, unknown>;
  validation?: {
    valid?: boolean;
    [key: string]: unknown;
  };
  actor_id?: string;
  created_at?: string;
  applied_at?: string | null;
}

export interface ConfigState {
  active: ConfigRevision | null;
  latest: ConfigRevision | null;
}

export interface CommandResult {
  entity_id: string;
  version: number;
  state: string;
  replayed: boolean;
  data: Record<string, unknown>;
}

export interface ConfigValidation {
  draft_id: string;
  valid: boolean;
  content_hash: string;
  revision: number;
  version: number;
  content: Record<string, unknown>;
}

export type HumanAuthorizationOperation =
  | "plan_approve"
  | "review_human_approve"
  | "config_apply_draft"
  | "task_cancel"
  | "dependency_waive"
  | "git_integrate_plan";

export interface HumanAuthorization {
  capability_token: string;
  operation: HumanAuthorizationOperation;
  resource_id: string | null;
  expires_in_seconds: number;
}

export interface BoardSnapshot {
  project: { name: string; path?: string };
  plan?: Plan | null;
  plans?: Plan[];
  tasks: Task[];
  columns: {
    backlog: Task[];
    eligible: Task[];
    in_progress: Task[];
    verifying: Task[];
    done: Task[];
  };
  attention: {
    blocked: Task[];
    stale: Task[];
    awaiting_review: Task[];
    conflicts: Task[];
  };
  agents: Agent[];
  runs: Run[];
  usage_totals?: UsageTotals;
  last_event_id?: string | number | null;
  wip?: { active: number; limit: number };
}

export type BoardColumn = keyof BoardSnapshot["columns"];

export interface TaskMoveIntentResult {
  task_id: string;
  version: number;
  source_column: BoardColumn | "canceled";
  target_column: BoardColumn;
  accepted: boolean;
  mutation_performed: false;
  required_command: "task_claim" | "task_report_result" | "review_decide" | null;
  message: string;
}

const emptyColumns = {
  backlog: [],
  eligible: [],
  in_progress: [],
  verifying: [],
  done: [],
};

const emptyAttention = {
  blocked: [],
  stale: [],
  awaiting_review: [],
  conflicts: [],
};

const emptyUsageTotals: UsageTotals = {
  input_tokens: 0,
  output_tokens: 0,
  total_tokens: 0,
  completed_duration_seconds: 0,
  reported_runs: 0,
  completed_runs: 0,
};

function normalizedSnapshot(value: Partial<BoardSnapshot>): BoardSnapshot {
  return {
    project: value.project ?? { name: "Projeto local" },
    plan: value.plan ?? null,
    plans: value.plans ?? (value.plan ? [value.plan] : []),
    tasks: value.tasks ?? Object.values(value.columns ?? emptyColumns).flat(),
    columns: { ...emptyColumns, ...value.columns },
    attention: { ...emptyAttention, ...value.attention },
    agents: value.agents ?? [],
    runs: value.runs ?? [],
    usage_totals: value.usage_totals ?? emptyUsageTotals,
    last_event_id: value.last_event_id,
    wip: value.wip,
  };
}

interface ApiErrorPayload {
  error?: {
    code?: string;
    message?: string;
    retryable?: boolean;
  };
  detail?:
    | string
    | Array<{
        loc?: Array<string | number>;
        msg?: string;
      }>;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code?: string;
  readonly retryable: boolean;

  constructor(message: string, status: number, code?: string, retryable = false) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.retryable = retryable;
  }
}

function csrfToken(): string {
  const projectKey = new URLSearchParams(
    typeof window === "undefined" ? "" : window.location.search,
  ).get("project") ?? cookieProjectKey();
  if (!projectKey || !/^[A-Za-z0-9_-]+$/.test(projectKey)) {
    throw new ApiError(
      "A sessão local não identifica o projeto. Reabra o dashboard pelo runtime.",
      401,
      "PROJECT_MISSING",
    );
  }
  const prefix = `agentboard_csrf_${projectKey}=`;
  const cookie = document.cookie
    .split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith(prefix));
  if (!cookie) {
    throw new ApiError(
      "A sessão local não possui token CSRF. Reabra o dashboard pelo runtime.",
      401,
      "CSRF_MISSING",
    );
  }
  return decodeURIComponent(cookie.slice(prefix.length));
}

function cookieProjectKey(): string | null {
  const matches = document.cookie
    .split(";")
    .map((part) => part.trim().match(/^agentboard_csrf_([A-Za-z0-9_-]+)=/))
    .filter((match): match is RegExpMatchArray => match !== null);
  return matches.length === 1 ? matches[0][1] : null;
}

async function responseError(response: Response): Promise<ApiError> {
  const fallback = `A API respondeu com status ${response.status}.`;
  const raw = await response.text().catch(() => "");
  if (!raw) return new ApiError(fallback, response.status);
  try {
    const payload = JSON.parse(raw) as ApiErrorPayload;
    const validationMessage = Array.isArray(payload.detail)
      ? payload.detail
          .map((issue) => {
            const location = issue.loc?.slice(1).join(".");
            return [location, issue.msg].filter(Boolean).join(": ");
          })
          .filter(Boolean)
          .join("; ")
      : payload.detail;
    return new ApiError(
      payload.error?.message || validationMessage || fallback,
      response.status,
      payload.error?.code,
      payload.error?.retryable ?? false,
    );
  } catch {
    return new ApiError(raw, response.status);
  }
}

async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await fetch(path, {
    ...options,
    headers: { Accept: "application/json" },
    credentials: "same-origin",
  });

  if (!response.ok) {
    throw await responseError(response);
  }

  return (await response.json()) as T;
}

export async function getBoard(signal?: AbortSignal): Promise<BoardSnapshot> {
  const snapshot = await request<Partial<BoardSnapshot>>("/api/v1/board", { signal });
  return normalizedSnapshot(snapshot);
}

export function getTask(taskId: string, signal?: AbortSignal): Promise<Task> {
  return request<Task>(`/api/v1/tasks/${encodeURIComponent(taskId)}`, { signal });
}

export function getPlans(signal?: AbortSignal): Promise<Plan[]> {
  return request<Plan[]>("/api/v1/plans", { signal });
}

export function getPlan(
  planId: string,
  revision: number,
  signal?: AbortSignal,
): Promise<PlanRevision> {
  const query = new URLSearchParams({ revision: String(revision) });
  return request<PlanRevision>(
    `/api/v1/plans/${encodeURIComponent(planId)}?${query.toString()}`,
    { signal },
  );
}

export function validatePlan(
  planId: string,
  revision: number,
  signal?: AbortSignal,
): Promise<PlanValidation> {
  const query = new URLSearchParams({ revision: String(revision) });
  return request<PlanValidation>(
    `/api/v1/plans/${encodeURIComponent(planId)}/validate?${query.toString()}`,
    { signal },
  );
}

export function getConfig(signal?: AbortSignal): Promise<ConfigRevision | null> {
  return request<ConfigRevision | null>("/api/v1/config", { signal });
}

export function getConfigState(signal?: AbortSignal): Promise<ConfigState> {
  return request<ConfigState>("/api/v1/config/state", { signal });
}

export function createIdempotencyKey(operation: string): string {
  const suffix =
    typeof crypto.randomUUID === "function"
      ? crypto.randomUUID()
      : Array.from(crypto.getRandomValues(new Uint8Array(16)), (value) =>
          value.toString(16).padStart(2, "0"),
        ).join("");
  return `dashboard:${operation}:${suffix}`;
}

async function browserMutation<T>(
  path: string,
  payload: Record<string, unknown>,
  capabilityToken?: string,
): Promise<T> {
  const headers: Record<string, string> = {
    Accept: "application/json",
    "Content-Type": "application/json",
    "X-AgentBoard-CSRF": csrfToken(),
  };
  if (capabilityToken) headers["X-AgentBoard-Capability"] = capabilityToken;
  const response = await fetch(path, {
    method: "POST",
    body: JSON.stringify(payload),
    headers,
    credentials: "same-origin",
  });
  if (!response.ok) throw await responseError(response);
  return (await response.json()) as T;
}

export function validateTaskMoveIntent(
  task: Task,
  targetColumn: BoardColumn,
  idempotencyKey: string,
): Promise<TaskMoveIntentResult> {
  if (task.version === undefined) {
    throw new ApiError(
      "A task não possui versão; atualize o quadro antes de movê-la.",
      409,
      "VERSION_MISSING",
    );
  }
  return browserMutation<TaskMoveIntentResult>("/api/v1/tasks/move-intent", {
    task_id: task.id,
    target_column: targetColumn,
    expected_version: task.version,
    idempotency_key: idempotencyKey,
  });
}

export function createConfigDraft(
  yamlText: string,
  expectedVersion: number,
  idempotencyKey: string,
): Promise<CommandResult> {
  return browserMutation<CommandResult>("/api/v1/config/drafts", {
    yaml_text: yamlText,
    expected_version: expectedVersion,
    idempotency_key: idempotencyKey,
  });
}

export function validateConfigDraft(
  draftId: string,
  signal?: AbortSignal,
): Promise<ConfigValidation> {
  return request<ConfigValidation>(
    `/api/v1/config/drafts/${encodeURIComponent(draftId)}/validate`,
    { signal },
  );
}

export function requestHumanAuthorization(
  operation: HumanAuthorizationOperation,
  resourceId: string | null,
): Promise<HumanAuthorization> {
  return browserMutation<HumanAuthorization>("/api/v1/authorizations", {
    operation,
    resource_id: resourceId,
  });
}

export function planApprovalResource(
  planId: string,
  revision: number,
  expectedVersion: number,
): string {
  return `plan:${planId}:revision:${revision}:version:${expectedVersion}`;
}

export function approvePlan(
  plan: Pick<Plan, "id" | "revision" | "version">,
  capabilityToken: string,
  idempotencyKey: string,
): Promise<CommandResult> {
  if (plan.version === undefined) {
    throw new ApiError(
      "A revisão não possui versão; atualize antes de aprová-la.",
      409,
      "VERSION_MISSING",
    );
  }
  return browserMutation<CommandResult>(
    `/api/v1/plans/${encodeURIComponent(plan.id)}/revisions/${plan.revision}/approve`,
    {
      expected_version: plan.version,
      idempotency_key: idempotencyKey,
    },
    capabilityToken,
  );
}

export function applyConfigDraft(
  validation: ConfigValidation,
  capabilityToken: string,
  idempotencyKey: string,
): Promise<CommandResult> {
  return browserMutation<CommandResult>(
    "/api/v1/config/apply",
    {
      draft_id: validation.draft_id,
      expected_fingerprint: validation.content_hash,
      expected_version: validation.version,
      idempotency_key: idempotencyKey,
    },
    capabilityToken,
  );
}

export function cancelTask(
  task: Task,
  reason: string,
  capabilityToken: string,
  idempotencyKey: string,
): Promise<CommandResult> {
  if (task.version === undefined) {
    throw new ApiError(
      "A task não possui versão; atualize os detalhes antes de agir.",
      409,
      "VERSION_MISSING",
    );
  }
  return browserMutation<CommandResult>(
    "/api/v1/tasks/cancel",
    {
      task_id: task.id,
      reason,
      expected_version: task.version,
      idempotency_key: idempotencyKey,
    },
    capabilityToken,
  );
}

export function createBoardEventSource(lastEventId?: string | number | null): EventSource {
  const query =
    lastEventId === undefined || lastEventId === null
      ? ""
      : `?last_event_id=${encodeURIComponent(String(lastEventId))}`;
  return new EventSource(`/api/v1/events/stream${query}`, { withCredentials: true });
}
