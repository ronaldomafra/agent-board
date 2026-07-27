import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type DragEvent,
  type ReactNode,
} from "react";
import {
  ApiError,
  createIdempotencyKey,
  getTask,
  validateTaskMoveIntent,
  type Agent,
  type BoardSnapshot,
  type Evidence,
  type Run,
  type Task,
} from "./api";
import { ConfigurationView } from "./ConfigurationView";
import { PlansView } from "./PlansView";
import { TaskActionPanel } from "./TaskActionPanel";
import {
  TasksView,
  type TaskAttentionFilter,
  type TaskFilters,
} from "./TasksView";
import { useBoard, type ConnectionState } from "./useBoard";

type View = "plans" | "tasks" | "board" | "agents" | "runs" | "config";
type ColumnKey = keyof BoardSnapshot["columns"];
type AttentionKey = keyof BoardSnapshot["attention"];

const columns: Array<{ key: ColumnKey; label: string; hint: string }> = [
  { key: "backlog", label: "Backlog", hint: "Aguardando elegibilidade" },
  { key: "eligible", label: "Elegíveis", hint: "Prontas para reserva" },
  { key: "in_progress", label: "Em execução", hint: "Run e lease ativos" },
  { key: "verifying", label: "Verificação", hint: "Evidência em revisão" },
  { key: "done", label: "Concluídas", hint: "Aceite registrado" },
];

const attentionLabels: Record<AttentionKey, { label: string; tone: string }> = {
  blocked: { label: "Bloqueadas", tone: "danger" },
  stale: { label: "Sem heartbeat", tone: "warning" },
  awaiting_review: { label: "Aguardando revisão", tone: "purple" },
  conflicts: { label: "Conflitos", tone: "danger" },
};

const defaultTaskFilters: TaskFilters = {
  text: "",
  status: "all",
  profile: "all",
  risk: "all",
  attention: "all",
};

function Icon({
  name,
  size = 18,
}: {
  name: "plan" | "task" | "board" | "agent" | "run" | "config" | "search" | "close" | "pulse" | "chevron" | "target";
  size?: number;
}) {
  const paths: Record<typeof name, ReactNode> = {
    plan: <><path d="M5 3h11l3 3v15H5Z" /><path d="M15 3v4h4M8 11h8M8 15h8" /></>,
    task: <><path d="M9 6h11M9 12h11M9 18h11" /><path d="m3.5 6 1 1 2-2M3.5 12l1 1 2-2M3.5 18l1 1 2-2" /></>,
    board: <><rect x="3" y="4" width="5" height="16" rx="1" /><rect x="10" y="4" width="5" height="10" rx="1" /><rect x="17" y="4" width="4" height="13" rx="1" /></>,
    agent: <><circle cx="12" cy="8" r="4" /><path d="M4.5 21a7.5 7.5 0 0 1 15 0" /></>,
    run: <><path d="m8 5 11 7-11 7Z" /><path d="M3 5v14" /></>,
    config: <><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1A1.7 1.7 0 0 0 9 4.6a1.7 1.7 0 0 0 1-1.6v-.2h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1Z" /></>,
    search: <><circle cx="10.5" cy="10.5" r="6.5" /><path d="m16 16 5 5" /></>,
    close: <><path d="m6 6 12 12M18 6 6 18" /></>,
    pulse: <><path d="M3 12h4l2.2-6 4.2 12 2.2-6H21" /></>,
    chevron: <><path d="m9 18 6-6-6-6" /></>,
    target: <><circle cx="12" cy="12" r="8" /><circle cx="12" cy="12" r="3" /></>,
  };
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {paths[name]}
    </svg>
  );
}

function taskTitle(task: Task) {
  return task.title || task.objective || task.id;
}

function formatRelative(value?: string) {
  if (!value) return "sem registro";
  const milliseconds = Date.now() - new Date(value).getTime();
  if (!Number.isFinite(milliseconds)) return value;
  const minutes = Math.max(0, Math.floor(milliseconds / 60_000));
  if (minutes < 1) return "agora";
  if (minutes < 60) return `há ${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `há ${hours} h`;
  return `há ${Math.floor(hours / 24)} d`;
}

function StatusDot({ status }: { status?: string }) {
  const normalized = (status || "unknown").toLowerCase();
  const healthy = ["online", "healthy", "running", "active", "accepted", "succeeded"].some((value) => normalized.includes(value));
  const warning = ["idle", "pending", "verifying", "reserved"].some((value) => normalized.includes(value));
  return <span className={`status-dot ${healthy ? "healthy" : warning ? "warning" : "muted"}`} />;
}

function ConnectionBadge({
  state,
  lastUpdatedAt,
}: {
  state: ConnectionState;
  lastUpdatedAt: Date | null;
}) {
  const labels: Record<ConnectionState, string> = {
    connecting: "Conectando",
    live: "Ao vivo",
    reconnecting: "Reconectando",
    offline: "Offline",
  };
  return (
    <div className={`connection ${state}`} title={lastUpdatedAt ? `Snapshot atualizado ${formatRelative(lastUpdatedAt.toISOString())}` : "Aguardando primeiro snapshot"}>
      <span className="connection-pulse" />
      <span>{labels[state]}</span>
    </div>
  );
}

function TaskCard({
  task,
  onOpen,
  onMoveIntent,
}: {
  task: Task;
  onOpen: (task: Task) => void;
  onMoveIntent: (task: Task, target: ColumnKey) => void;
}) {
  const checkpoints = task.checkpoints ?? [];
  const completed = checkpoints.filter((item) => ["PASSED", "RECORDED", "DONE"].includes((item.status || "").toUpperCase())).length;
  const isBlocked = Boolean(task.blocked || task.blocked_reason);

  return (
    <div className="task-card-shell">
      <button
        className={`task-card ${isBlocked ? "is-blocked" : ""}`}
        draggable
        onDragStart={(event) => {
          event.dataTransfer.effectAllowed = "move";
          event.dataTransfer.setData("text/plain", task.id);
        }}
        onClick={() => onOpen(task)}
        aria-label={`Abrir detalhes de ${taskTitle(task)}. Também pode ser arrastada para validar uma intenção de movimento.`}
      >
        <div className="task-card-topline">
          <span className={`priority ${(task.priority || "P2").toLowerCase()}`}>{task.priority || "P2"}</span>
          <span className="task-id">{task.id}</span>
          <span className={`risk ${(task.risk || "LOW").toLowerCase()}`}>{task.risk || "LOW"}</span>
        </div>
        <strong>{taskTitle(task)}</strong>
        {task.scope && <p>{Array.isArray(task.scope) ? task.scope.join(" · ") : task.scope}</p>}
        {isBlocked && <span className="blocker-label">{task.blocked_reason || task.reasons?.[0] || "Bloqueada pelo serviço"}</span>}
        <div className="task-card-meta">
          {task.agent_id ? <span className="agent-token"><StatusDot status="active" />{task.agent_id}</span> : <span>{task.profile || "perfil aberto"}</span>}
          {checkpoints.length > 0 && (
            <span className="checkpoint-count" title="Checkpoints objetivos registrados">
              <Icon name="target" size={14} /> {completed}/{checkpoints.length}
            </span>
          )}
        </div>
      </button>
      <label className="task-move-control">
        <span className="sr-only">Validar intenção de movimento para {taskTitle(task)}</span>
        <select
          value=""
          onChange={(event) => {
            const target = event.target.value as ColumnKey;
            if (target) onMoveIntent(task, target);
          }}
          aria-label={`Validar movimento de ${taskTitle(task)} pelo teclado`}
        >
          <option value="">Validar movimento…</option>
          {columns.map((column) => (
            <option value={column.key} key={column.key}>
              {column.label}
            </option>
          ))}
        </select>
      </label>
    </div>
  );
}

function BoardColumn({
  definition,
  tasks,
  onOpen,
  onMoveIntent,
  onDropTask,
}: {
  definition: (typeof columns)[number];
  tasks: Task[];
  onOpen: (task: Task) => void;
  onMoveIntent: (task: Task, target: ColumnKey) => void;
  onDropTask: (taskId: string, target: ColumnKey) => void;
}) {
  const [dropTarget, setDropTarget] = useState(false);
  const onDrop = (event: DragEvent<HTMLElement>) => {
    event.preventDefault();
    setDropTarget(false);
    const taskId = event.dataTransfer.getData("text/plain").trim();
    if (taskId) onDropTask(taskId, definition.key);
  };
  return (
    <section
      className={`board-column column-${definition.key} ${dropTarget ? "is-drop-target" : ""}`}
      aria-labelledby={`column-${definition.key}`}
      onDragEnter={(event) => {
        event.preventDefault();
        setDropTarget(true);
      }}
      onDragOver={(event) => event.preventDefault()}
      onDragLeave={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
          setDropTarget(false);
        }
      }}
      onDrop={onDrop}
    >
      <div className="column-header">
        <div>
          <h2 id={`column-${definition.key}`}>{definition.label}</h2>
          <p>{definition.hint}</p>
        </div>
        <span className="column-count">{tasks.length}</span>
      </div>
      <div className="task-stack">
        {tasks.map((task) => (
          <TaskCard
            key={task.id}
            task={task}
            onOpen={onOpen}
            onMoveIntent={onMoveIntent}
          />
        ))}
        {tasks.length === 0 && (
          <div className="column-empty">
            <span />
            <p>Nenhuma task</p>
          </div>
        )}
      </div>
    </section>
  );
}

function AttentionRail({
  snapshot,
  onFilter,
}: {
  snapshot: BoardSnapshot;
  onFilter: (filter: Exclude<TaskAttentionFilter, "all">) => void;
}) {
  const groups = (Object.keys(attentionLabels) as AttentionKey[])
    .map((key) => ({ key, tasks: snapshot.attention[key] }))
    .filter(({ tasks }) => tasks.length > 0);
  const taskCount = new Set(
    groups.flatMap(({ tasks }) => tasks.map((task) => task.id)),
  ).size;

  if (groups.length === 0) {
    return (
      <aside className="attention-rail calm" aria-label="Atenção">
        <span className="attention-icon"><Icon name="pulse" /></span>
        <div><strong>Fluxo saudável</strong><p>Nenhum desvio operacional exige atenção agora.</p></div>
      </aside>
    );
  }

  return (
    <aside className="attention-rail" aria-label="Itens que exigem atenção">
      <div className="attention-intro">
        <span className="attention-icon"><Icon name="pulse" /></span>
        <div><strong>Requer atenção</strong><p>{taskCount} {taskCount === 1 ? "task sinalizada" : "tasks sinalizadas"}</p></div>
      </div>
      <div className="attention-items">
        {groups.map(({ key, tasks }) => (
          <button
            type="button"
            key={key}
            onClick={() => onFilter(key)}
            aria-label={`Filtrar ${tasks.length} ${tasks.length === 1 ? "task" : "tasks"} em ${attentionLabels[key].label}`}
          >
            <span className={`attention-tag ${attentionLabels[key].tone}`}>{attentionLabels[key].label}</span>
            <strong>{tasks.length} {tasks.length === 1 ? "task" : "tasks"}</strong>
            <small>{taskTitle(tasks[0])}{tasks.length > 1 ? ` +${tasks.length - 1}` : ""}</small>
            <Icon name="chevron" size={14} />
          </button>
        ))}
      </div>
    </aside>
  );
}

function AgentsView({ agents, onTask }: { agents: Agent[]; onTask: (taskId: string) => void }) {
  return (
    <section className="entity-view" aria-labelledby="agents-title">
      <div className="section-heading">
        <div><p className="eyebrow">Capacidade local</p><h2 id="agents-title">Agentes e instâncias</h2></div>
        <span>{agents.length} registrados</span>
      </div>
      {agents.length === 0 ? <EmptyState title="Nenhum agente registrado" body="As instâncias aparecerão aqui depois que o orquestrador confirmar uma reserva." /> : (
        <div className="entity-grid">
          {agents.map((agent) => (
            <article className="entity-card" key={agent.id}>
              <div className="entity-card-head"><span className="avatar">{(agent.name || agent.id).slice(0, 2).toUpperCase()}</span><StatusDot status={agent.health || agent.status || agent.assignment_status} /></div>
              <h3>{agent.name || agent.id}</h3>
              <p>{agent.profile || "Perfil não informado"}</p>
              <dl>
                <div><dt>Estado</dt><dd>{agent.status || agent.health || agent.assignment_status || "desconhecido"}</dd></div>
                <div><dt>Heartbeat</dt><dd>{formatRelative(agent.last_heartbeat || agent.last_heartbeat_at)}</dd></div>
              </dl>
              {agent.current_task_id && <button className="text-button" onClick={() => onTask(agent.current_task_id!)}>Abrir task {agent.current_task_id}<Icon name="chevron" size={14} /></button>}
            </article>
          ))}
        </div>
      )}
    </section>
  );
}

function RunsView({ runs, onTask }: { runs: Run[]; onTask: (taskId: string) => void }) {
  return (
    <section className="entity-view" aria-labelledby="runs-title">
      <div className="section-heading">
        <div><p className="eyebrow">Tentativas auditáveis</p><h2 id="runs-title">Execuções</h2></div>
        <span>{runs.length} no snapshot</span>
      </div>
      {runs.length === 0 ? <EmptyState title="Nenhuma execução registrada" body="Runs ativos e concluídos aparecerão aqui com checkpoint e evidência." /> : (
        <div className="run-table-wrap">
          <table className="run-table">
            <thead><tr><th>Run</th><th>Task</th><th>Agente</th><th>Estado</th><th>Início</th><th>Último checkpoint</th></tr></thead>
            <tbody>
              {runs.map((run) => (
                <tr key={run.id}>
                  <td><span className="mono">{run.id}</span>{run.attempt && <small>Tentativa {run.attempt}</small>}</td>
                  <td>{run.task_id ? <button onClick={() => onTask(run.task_id!)}>{run.task_title || run.task_id}</button> : "—"}</td>
                  <td>{run.agent_id || "—"}</td>
                  <td><span className="run-status"><StatusDot status={run.status} />{run.status || "desconhecido"}</span></td>
                  <td>{formatRelative(run.started_at)}</td>
                  <td>{run.latest_checkpoint || run.failure_reason || "Sem checkpoint"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function EmptyState({ title, body }: { title: string; body: string }) {
  return <div className="page-empty"><span><Icon name="target" size={28} /></span><h3>{title}</h3><p>{body}</p></div>;
}

function DetailSection({ title, children }: { title: string; children: ReactNode }) {
  return <section className="detail-section"><h3>{title}</h3>{children}</section>;
}

function EvidenceList({ evidence }: { evidence: Evidence[] }) {
  if (evidence.length === 0) return <p className="detail-muted">Nenhuma evidência registrada.</p>;
  return <ul className="evidence-list">{evidence.map((item, index) => <li key={item.id || index}><span>{item.kind || "EVIDENCE"}</span><div><strong>{item.summary || item.value || (typeof item.payload?.summary === "string" ? item.payload.summary : "Evidência registrada")}</strong>{(item.recorded_at || item.created_at) && <small>{formatRelative(item.recorded_at || item.created_at)}</small>}</div></li>)}</ul>;
}

function TaskDrawer({
  task,
  open,
  onClose,
  onTaskCanceled,
}: {
  task: Task | null;
  open: boolean;
  onClose: () => void;
  onTaskCanceled: (task: Task) => void | Promise<void>;
}) {
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    closeRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  if (!open || !task) return null;
  const acceptance = task.acceptance_criteria ?? task.acceptance ?? [];

  return (
    <div className="drawer-layer">
      <button className="drawer-backdrop" onClick={onClose} aria-label="Fechar detalhes" />
      <aside className="task-drawer" role="dialog" aria-modal="true" aria-labelledby="drawer-title">
        <div className="drawer-head">
          <div><span className="task-id">{task.id}</span><h2 id="drawer-title">{taskTitle(task)}</h2></div>
          <button className="icon-button" onClick={onClose} ref={closeRef} aria-label="Fechar"><Icon name="close" /></button>
        </div>
        <div className="drawer-body">
          <div className="drawer-badges">
            <span className={`priority ${(task.priority || "P2").toLowerCase()}`}>{task.priority || "P2"}</span>
            <span className={`risk ${(task.risk || "LOW").toLowerCase()}`}>Risco {task.risk || "LOW"}</span>
            <span className="phase-badge">{task.phase || task.status || "BACKLOG"}</span>
          </div>
          <DetailSection title="Objetivo"><p>{task.objective || task.title || "Objetivo não informado."}</p></DetailSection>
          <DetailSection title="Escopo"><p>{Array.isArray(task.scope) ? task.scope.join("\n") : task.scope || "Escopo não informado."}</p>{task.paths && <div className="path-list">{task.paths.map((path) => <code key={path}>{path}</code>)}</div>}</DetailSection>
          <DetailSection title="Critérios de aceite">
            {acceptance.length ? <ul className="check-list">{acceptance.map((item) => <li key={item}>{item}</li>)}</ul> : <p className="detail-muted">Nenhum critério informado.</p>}
          </DetailSection>
          <DetailSection title="Testes">
            {task.tests?.length ? <ul className="check-list tests">{task.tests.map((item) => <li key={item}>{item}</li>)}</ul> : <p className="detail-muted">Nenhum teste informado.</p>}
          </DetailSection>
          <DetailSection title="Dependências">
            {task.dependencies?.length ? <ul className="dependency-list">{task.dependencies.map((dependency, index) => <li key={dependency.task_id || dependency.id || index}><span>{dependency.type || "REQUIRES"}</span><strong>{dependency.title || dependency.task_id || dependency.id}</strong><em>{dependency.waived ? "dispensada" : dependency.satisfied ? "satisfeita" : "pendente"}</em></li>)}</ul> : <p className="detail-muted">Sem dependências.</p>}
          </DetailSection>
          <DetailSection title="Checkpoints objetivos">
            {task.checkpoints?.length ? <ol className="checkpoint-list">{task.checkpoints.map((checkpoint, index) => <li key={checkpoint.id || index}><span className={`checkpoint-marker ${(checkpoint.status || "pending").toLowerCase()}`} /><div><strong>{checkpoint.summary || checkpoint.title || checkpoint.kind || "Checkpoint"}</strong><small>{checkpoint.status || checkpoint.kind || "REGISTRADO"}{checkpoint.created_at ? ` · ${formatRelative(checkpoint.created_at)}` : ""}</small></div></li>)}</ol> : <p className="detail-muted">Nenhum checkpoint registrado.</p>}
          </DetailSection>
          <DetailSection title="Lease e run">
            <dl className="detail-grid">
              <div><dt>Agente</dt><dd>{task.agent_id || task.lease?.owner || "não atribuído"}</dd></div>
              <div><dt>Geração</dt><dd>{task.lease?.generation ?? "—"}</dd></div>
              <div><dt>Expira</dt><dd>{task.lease?.expires_at ? formatRelative(task.lease.expires_at) : "sem lease"}</dd></div>
              <div><dt>Run</dt><dd>{task.run?.id || "—"}</dd></div>
            </dl>
          </DetailSection>
          <DetailSection title="Revisão">
            <dl className="detail-grid">
              <div><dt>Decisão</dt><dd>{task.review?.status || "PENDING"}</dd></div>
              <div><dt>Reviewer</dt><dd>{task.review?.reviewer || task.review?.reviewer_id || "—"}</dd></div>
            </dl>
            {(task.review?.summary || task.review?.decision_reason) && <p>{task.review.summary || task.review.decision_reason}</p>}
          </DetailSection>
          <DetailSection title="Evidências"><EvidenceList evidence={task.evidence ?? []} /></DetailSection>
          <TaskActionPanel task={task} onCanceled={onTaskCanceled} />
        </div>
        <footer className="drawer-footer">
          <span>Versão {task.version ?? "—"}</span>
          <p>Transições são decididas e auditadas pelo serviço de domínio.</p>
        </footer>
      </aside>
    </div>
  );
}

export function App() {
  const { snapshot, error, loading, connection, lastUpdatedAt, refresh } = useBoard();
  const [view, setView] = useState<View>("board");
  const [search, setSearch] = useState("");
  const [taskFilters, setTaskFilters] = useState<TaskFilters>({
    ...defaultTaskFilters,
  });
  const [taskFocusToken, setTaskFocusToken] = useState(0);
  const [selectedTask, setSelectedTask] = useState<Task | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [detailError, setDetailError] = useState(false);
  const [intentFeedback, setIntentFeedback] = useState<{
    tone: "pending" | "accepted" | "rejected";
    message: string;
  } | null>(null);

  const openTask = (task: Task) => {
    setSelectedTask(task);
    setDrawerOpen(true);
    setDetailError(false);
    const controller = new AbortController();
    void getTask(task.id, controller.signal)
      .then((detail) => setSelectedTask(detail))
      .catch(() => setDetailError(true));
  };

  const openTaskById = (id: string) => {
    const allTasks = snapshot?.tasks ?? [];
    const found = allTasks.find((task) => task.id === id);
    openTask(found ?? { id, title: id });
  };

  const filteredColumns = useMemo(() => {
    if (!snapshot) return null;
    const query = search.trim().toLocaleLowerCase("pt-BR");
    if (!query) return snapshot.columns;
    return Object.fromEntries(
      Object.entries(snapshot.columns).map(([key, tasks]) => [
        key,
        tasks.filter((task) => [task.id, task.title, task.objective, task.profile, task.agent_id].some((value) => value?.toLocaleLowerCase("pt-BR").includes(query))),
      ]),
    ) as BoardSnapshot["columns"];
  }, [search, snapshot]);

  const taskCount = snapshot?.tasks.length ?? 0;
  const activeCount = snapshot
    ? snapshot.wip?.active ??
      snapshot.columns.in_progress.length + snapshot.columns.verifying.length
    : 0;
  const activeAgentTaskCount = snapshot
    ? new Set(
        snapshot.agents
          .map((agent) => agent.current_task_id)
          .filter(Boolean),
      ).size
    : 0;
  const planName = snapshot?.plan?.name || snapshot?.plan?.title || "Sem plano ativo";

  const navigateToTasks = (filters: TaskFilters) => {
    setTaskFilters(filters);
    setView("tasks");
    setTaskFocusToken((value) => value + 1);
  };

  const navigateToTaskPreset = (filters: Partial<TaskFilters> = {}) => {
    navigateToTasks({ ...defaultTaskFilters, ...filters });
  };

  const handleTaskCanceled = async (task: Task) => {
    setSelectedTask(task);
    await refresh();
  };

  const handleMoveIntent = async (task: Task, target: ColumnKey) => {
    setIntentFeedback({
      tone: "pending",
      message: `Validando movimento de ${task.id}…`,
    });
    const idempotencyKey = createIdempotencyKey(`move-intent:${task.id}:${target}`);
    try {
      const result = await validateTaskMoveIntent(task, target, idempotencyKey);
      const command = result.required_command
        ? ` Próximo comando: ${result.required_command}.`
        : "";
      setIntentFeedback({
        tone: result.accepted ? "accepted" : "rejected",
        message: `${result.message}${command}`,
      });
    } catch (caught) {
      const message =
        caught instanceof Error
          ? caught.message
          : "Não foi possível validar o movimento.";
      setIntentFeedback({ tone: "rejected", message });
      if (caught instanceof ApiError && caught.retryable) {
        await refresh();
      }
    }
  };

  const handleDropTask = (taskId: string, target: ColumnKey) => {
    const task = snapshot?.tasks.find((candidate) => candidate.id === taskId);
    if (!task) {
      setIntentFeedback({
        tone: "rejected",
        message: "A task arrastada não pertence mais ao snapshot atual.",
      });
      return;
    }
    void handleMoveIntent(task, target);
  };

  return (
    <div className="app-frame">
      <aside className="sidebar">
        <div className="brand"><span className="brand-mark"><span /><span /><span /></span><strong>AgentBoard</strong></div>
        <nav aria-label="Navegação principal">
          <button className={view === "plans" ? "active" : ""} onClick={() => setView("plans")}><Icon name="plan" /><span>Planos</span>{snapshot && <em>{snapshot.plans?.length ?? 0}</em>}</button>
          <button className={view === "tasks" ? "active" : ""} onClick={() => navigateToTasks(taskFilters)}><Icon name="task" /><span>Tasks</span>{snapshot && <em>{taskCount}</em>}</button>
          <button className={view === "board" ? "active" : ""} onClick={() => setView("board")}><Icon name="board" /><span>Quadro</span></button>
          <button className={view === "agents" ? "active" : ""} onClick={() => setView("agents")}><Icon name="agent" /><span>Agentes</span>{snapshot && <em>{snapshot.agents.length}</em>}</button>
          <button className={view === "runs" ? "active" : ""} onClick={() => setView("runs")}><Icon name="run" /><span>Execuções</span>{snapshot && <em>{snapshot.runs.length}</em>}</button>
          <button className={view === "config" ? "active" : ""} onClick={() => setView("config")}><Icon name="config" /><span>Configuração</span></button>
        </nav>
        <div className="sidebar-foot"><span>LOCAL CONTROL PLANE</span><small>Autoridade no serviço</small></div>
      </aside>

      <main className="main-content">
        <header className="topbar">
          <div className="project-context">
            <p>{snapshot?.project.name || "AgentBoard local"}</p>
            <div className="plan-line">
              <h1>{planName}</h1>
              {snapshot?.plan?.revision !== undefined && <span>rev. {snapshot.plan.revision}</span>}
              {snapshot?.plan?.status && <span className="plan-status">{snapshot.plan.status}</span>}
            </div>
          </div>
          <div className="topbar-actions">
            {view === "board" && (
              <label className="search">
                <Icon name="search" size={17} />
                <span className="sr-only">Buscar tasks</span>
                <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Buscar task, agente, perfil…" />
                {search && <button onClick={() => setSearch("")} aria-label="Limpar busca"><Icon name="close" size={14} /></button>}
              </label>
            )}
            <ConnectionBadge state={connection} lastUpdatedAt={lastUpdatedAt} />
          </div>
        </header>

        {error && (
          <div className="error-banner" role="alert">
            <div><strong>Snapshot indisponível</strong><p>{error}</p></div>
            <button onClick={() => void refresh()}>Tentar novamente</button>
          </div>
        )}

        {loading && !snapshot ? (
          <div className="loading-board" aria-label="Carregando quadro">{columns.map((column) => <span key={column.key} />)}</div>
        ) : snapshot ? (
          <>
            {view === "board" && filteredColumns && (
              <>
                <section className="metrics" aria-label="Resumo do fluxo">
                  <button type="button" className="metric-card" onClick={() => navigateToTaskPreset()} aria-label={`Ver todas as ${taskCount} tasks`}>
                    <span>Tasks no plano</span><strong>{taskCount}</strong><small>Ver tasks</small>
                  </button>
                  <button type="button" className="metric-card" onClick={() => navigateToTaskPreset({ status: "wip" })} aria-label={`Filtrar ${activeCount} tasks em WIP`}>
                    <span>WIP ativo</span>
                    <strong>{snapshot.wip ? `${activeCount}/${snapshot.wip.limit}` : activeCount}</strong>
                    <small>Filtrar WIP</small>
                  </button>
                  <button type="button" className="metric-card" onClick={() => navigateToTaskPreset({ status: "eligible" })} aria-label={`Filtrar ${snapshot.columns.eligible.length} tasks elegíveis`}>
                    <span>Elegíveis agora</span><strong>{snapshot.columns.eligible.length}</strong><small>Filtrar elegíveis</small>
                  </button>
                  <button type="button" className="metric-card" onClick={() => navigateToTaskPreset({ status: "active_agent" })} aria-label={`Filtrar ${activeAgentTaskCount} tasks com agente ativo`}>
                    <span>Com agente ativo</span><strong>{activeAgentTaskCount}</strong><small>Filtrar atribuições</small>
                  </button>
                </section>
                <AttentionRail
                  snapshot={snapshot}
                  onFilter={(attention) =>
                    navigateToTaskPreset({ attention })
                  }
                />
                <aside className="interaction-note">
                  <strong>Drag é apenas uma intenção</strong>
                  <span>Arraste ou use o seletor da task. O servidor valida a versão e informa o comando necessário; nenhuma fase muda diretamente no browser.</span>
                </aside>
                {intentFeedback && (
                  <p
                    className={`intent-feedback ${intentFeedback.tone}`}
                    role="status"
                    aria-live="polite"
                  >
                    {intentFeedback.message}
                  </p>
                )}
                <section className="board" aria-label="Quadro de execução">
                  {columns.map((definition) => (
                    <BoardColumn
                      key={definition.key}
                      definition={definition}
                      tasks={filteredColumns[definition.key]}
                      onOpen={openTask}
                      onMoveIntent={(task, target) => void handleMoveIntent(task, target)}
                      onDropTask={handleDropTask}
                    />
                  ))}
                </section>
                {search && Object.values(filteredColumns).every((tasks) => tasks.length === 0) && <p className="search-empty">Nenhuma task corresponde a “{search}”.</p>}
              </>
            )}
            {view === "plans" && <PlansView />}
            {view === "tasks" && (
              <TasksView
                snapshot={snapshot}
                filters={taskFilters}
                onFiltersChange={setTaskFilters}
                onOpenTask={openTask}
                focusToken={taskFocusToken}
              />
            )}
            {view === "agents" && <AgentsView agents={snapshot.agents} onTask={openTaskById} />}
            {view === "runs" && <RunsView runs={snapshot.runs} onTask={openTaskById} />}
            {view === "config" && <ConfigurationView onApplied={refresh} />}
          </>
        ) : (
          <EmptyState title="O runtime ainda não respondeu" body="Inicie o runtime AgentBoard neste projeto e tente atualizar o snapshot." />
        )}
      </main>

      <TaskDrawer
        task={selectedTask}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        onTaskCanceled={handleTaskCanceled}
      />
      {detailError && drawerOpen && <div className="detail-toast" role="status">Detalhes completos indisponíveis; exibindo o snapshot do quadro.</div>}
    </div>
  );
}
