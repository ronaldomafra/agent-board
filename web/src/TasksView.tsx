import { useEffect, useMemo, useRef } from "react";
import type { BoardSnapshot, Task } from "./api";

type ColumnKey = keyof BoardSnapshot["columns"];
type TaskLocation = ColumnKey | "outside_board";

export type TaskStatusFilter =
  | "all"
  | "backlog"
  | "eligible"
  | "assigned"
  | "wip"
  | "in_progress"
  | "verifying"
  | "blocked"
  | "rework"
  | "done"
  | "canceled"
  | "active_agent";

export type TaskAttentionFilter = "all" | keyof BoardSnapshot["attention"];

export interface TaskFilters {
  text: string;
  status: TaskStatusFilter;
  profile: string;
  risk: string;
  attention: TaskAttentionFilter;
}

const defaultTaskFilters: TaskFilters = {
  text: "",
  status: "all",
  profile: "all",
  risk: "all",
  attention: "all",
};

const NO_PROFILE = "__no_profile__";

const columnOrder: ColumnKey[] = [
  "backlog",
  "eligible",
  "in_progress",
  "verifying",
  "done",
];

const columnLabels: Record<TaskLocation, string> = {
  backlog: "Backlog",
  eligible: "Elegível",
  in_progress: "Em execução",
  verifying: "Verificação",
  done: "Concluída",
  outside_board: "Fora do Kanban",
};

const attentionLabels: Record<
  keyof BoardSnapshot["attention"],
  { label: string; tone: string }
> = {
  blocked: { label: "Bloqueada", tone: "danger" },
  stale: { label: "Sem heartbeat", tone: "warning" },
  awaiting_review: { label: "Aguardando revisão", tone: "purple" },
  conflicts: { label: "Conflito", tone: "danger" },
};

const statusOptions: Array<{ value: TaskStatusFilter; label: string }> = [
  { value: "all", label: "Todos os status" },
  { value: "backlog", label: "Coluna Backlog" },
  { value: "eligible", label: "Elegível (READY)" },
  { value: "assigned", label: "Atribuída (ASSIGNED)" },
  { value: "wip", label: "WIP ativo" },
  { value: "in_progress", label: "Em execução" },
  { value: "verifying", label: "Em verificação" },
  { value: "blocked", label: "Bloqueada" },
  { value: "rework", label: "Em rework" },
  { value: "done", label: "Concluída" },
  { value: "canceled", label: "Cancelada" },
  { value: "active_agent", label: "Com agente ativo" },
];

const attentionOptions: Array<{
  value: TaskAttentionFilter;
  label: string;
}> = [
  { value: "all", label: "Toda atenção" },
  { value: "blocked", label: "Bloqueadas" },
  { value: "stale", label: "Sem heartbeat" },
  { value: "awaiting_review", label: "Aguardando revisão" },
  { value: "conflicts", label: "Conflitos" },
];

interface TaskRow {
  task: Task;
  column: TaskLocation;
  attention: Array<keyof BoardSnapshot["attention"]>;
}

function taskTitle(task: Task): string {
  return task.title || task.objective || task.id;
}

function taskProfile(task: Task): string | null {
  return task.profile || task.suggested_profile || null;
}

function normalized(value: string | undefined): string {
  return (value || "").toLocaleLowerCase("pt-BR");
}

function displayStatus(task: Task, column: TaskLocation): string {
  return task.status || task.phase || task.state || columnLabels[column];
}

function statusTone(status: string): string {
  return status.toLocaleLowerCase("en-US").replace(/[^a-z0-9_-]/g, "-");
}

function matchesStatus(
  row: TaskRow,
  filter: TaskStatusFilter,
  activeTaskIds: Set<string>,
): boolean {
  const status = (row.task.status || "").toUpperCase();
  const phase = (row.task.phase || row.task.state || "").toUpperCase();

  switch (filter) {
    case "all":
      return true;
    case "backlog":
    case "eligible":
    case "in_progress":
    case "verifying":
    case "done":
      return row.column === filter;
    case "assigned":
      return status === "ASSIGNED";
    case "wip":
      return (
        activeTaskIds.has(row.task.id) ||
        row.column === "in_progress" ||
        row.column === "verifying"
      );
    case "blocked":
      return row.attention.includes("blocked") || status === "BLOCKED";
    case "rework":
      return status === "REWORK";
    case "canceled":
      return status === "CANCELED" || phase === "CANCELED";
    case "active_agent":
      return activeTaskIds.has(row.task.id);
  }
}

function textMatches(row: TaskRow, query: string): boolean {
  if (!query) return true;
  const scope = Array.isArray(row.task.scope)
    ? row.task.scope.join(" ")
    : row.task.scope;
  const values = [
    row.task.id,
    row.task.title,
    row.task.objective,
    scope,
    taskProfile(row.task) || undefined,
    row.task.agent_id,
    row.task.blocked_reason,
    ...(row.task.paths || []),
    ...(row.task.reasons || []),
  ];
  return values.some((value) => normalized(value).includes(query));
}

function activeAgentTaskIds(snapshot: BoardSnapshot): Set<string> {
  return new Set(
    snapshot.agents
      .map((agent) => agent.current_task_id)
      .filter((taskId): taskId is string => Boolean(taskId)),
  );
}

export function TasksView({
  snapshot,
  filters,
  onFiltersChange,
  onOpenTask,
  focusToken = 0,
}: {
  snapshot: BoardSnapshot;
  filters: TaskFilters;
  onFiltersChange: (filters: TaskFilters) => void;
  onOpenTask: (task: Task) => void;
  focusToken?: number;
}) {
  const titleRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    if (focusToken > 0) titleRef.current?.focus();
  }, [focusToken]);

  const attentionByTask = useMemo(() => {
    const result = new Map<
      string,
      Array<keyof BoardSnapshot["attention"]>
    >();
    (
      Object.entries(snapshot.attention) as Array<
        [keyof BoardSnapshot["attention"], Task[]]
      >
    ).forEach(([key, tasks]) => {
      tasks.forEach((task) => {
        const values = result.get(task.id) || [];
        if (!values.includes(key)) values.push(key);
        result.set(task.id, values);
      });
    });
    return result;
  }, [snapshot.attention]);

  const rows = useMemo<TaskRow[]>(
    () => {
      const columnByTask = new Map<string, ColumnKey>();
      columnOrder.forEach((column) => {
        snapshot.columns[column].forEach((task) => {
          columnByTask.set(task.id, column);
        });
      });
      const columnTasks = columnOrder.flatMap(
        (column) => snapshot.columns[column],
      );
      const tasks = snapshot.tasks.length > 0 ? snapshot.tasks : columnTasks;
      return tasks.map((task) => ({
        task,
        column: columnByTask.get(task.id) || "outside_board",
        attention: attentionByTask.get(task.id) || [],
      }));
    },
    [attentionByTask, snapshot.columns, snapshot.tasks],
  );

  const profiles = useMemo(
    () =>
      Array.from(
        new Set(rows.map(({ task }) => taskProfile(task) || NO_PROFILE)),
      ).sort((left, right) => {
        if (left === NO_PROFILE) return 1;
        if (right === NO_PROFILE) return -1;
        return left.localeCompare(right, "pt-BR");
      }),
    [rows],
  );

  const risks = useMemo(
    () =>
      Array.from(
        new Set(rows.map(({ task }) => (task.risk || "LOW").toUpperCase())),
      ).sort((left, right) => {
        const order = ["LOW", "MEDIUM", "HIGH", "CRITICAL"];
        const leftIndex = order.indexOf(left);
        const rightIndex = order.indexOf(right);
        if (leftIndex === -1 && rightIndex === -1) {
          return left.localeCompare(right, "pt-BR");
        }
        if (leftIndex === -1) return 1;
        if (rightIndex === -1) return -1;
        return leftIndex - rightIndex;
      }),
    [rows],
  );

  const activeTaskIds = useMemo(
    () => activeAgentTaskIds(snapshot),
    [snapshot],
  );
  const query = normalized(filters.text.trim());
  const filteredRows = rows.filter((row) => {
    const profile = taskProfile(row.task) || NO_PROFILE;
    const risk = (row.task.risk || "LOW").toUpperCase();
    return (
      matchesStatus(row, filters.status, activeTaskIds) &&
      (filters.profile === "all" || filters.profile === profile) &&
      (filters.risk === "all" || filters.risk === risk) &&
      (filters.attention === "all" ||
        row.attention.includes(filters.attention)) &&
      textMatches(row, query)
    );
  });

  const activeFilterCount = [
    Boolean(filters.text.trim()),
    filters.status !== "all",
    filters.profile !== "all",
    filters.risk !== "all",
    filters.attention !== "all",
  ].filter(Boolean).length;

  const updateFilter = <Key extends keyof TaskFilters>(
    key: Key,
    value: TaskFilters[Key],
  ) => {
    onFiltersChange({ ...filters, [key]: value });
  };

  return (
    <section className="tasks-view" aria-labelledby="tasks-title">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Projeção operacional</p>
          <h2 id="tasks-title" ref={titleRef} tabIndex={-1}>
            Tasks
          </h2>
        </div>
        <span aria-live="polite">
          {filteredRows.length} de {rows.length}
        </span>
      </div>

      <div
        className="task-filters"
        role="search"
        aria-label="Filtros de tasks"
      >
        <label className="task-filter task-filter-text">
          <span>Texto</span>
          <input
            type="search"
            value={filters.text}
            onChange={(event) => updateFilter("text", event.target.value)}
            placeholder="ID, título, escopo, caminho…"
            aria-controls="task-results"
          />
        </label>

        <label className="task-filter">
          <span>Status</span>
          <select
            value={filters.status}
            onChange={(event) =>
              updateFilter("status", event.target.value as TaskStatusFilter)
            }
            aria-controls="task-results"
          >
            {statusOptions.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>

        <label className="task-filter">
          <span>Perfil</span>
          <select
            value={filters.profile}
            onChange={(event) => updateFilter("profile", event.target.value)}
            aria-controls="task-results"
          >
            <option value="all">Todos os perfis</option>
            {profiles.map((profile) => (
              <option key={profile} value={profile}>
                {profile === NO_PROFILE ? "Sem perfil" : profile}
              </option>
            ))}
          </select>
        </label>

        <label className="task-filter">
          <span>Risco</span>
          <select
            value={filters.risk}
            onChange={(event) => updateFilter("risk", event.target.value)}
            aria-controls="task-results"
          >
            <option value="all">Todos os riscos</option>
            {risks.map((risk) => (
              <option key={risk} value={risk}>
                {risk}
              </option>
            ))}
          </select>
        </label>

        <label className="task-filter">
          <span>Atenção</span>
          <select
            value={filters.attention}
            onChange={(event) =>
              updateFilter(
                "attention",
                event.target.value as TaskAttentionFilter,
              )
            }
            aria-controls="task-results"
          >
            {attentionOptions.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>

        <button
          type="button"
          className="secondary-button clear-task-filters"
          onClick={() => onFiltersChange({ ...defaultTaskFilters })}
          disabled={activeFilterCount === 0}
        >
          Limpar {activeFilterCount > 0 ? `(${activeFilterCount})` : ""}
        </button>
      </div>

      <p className="tasks-authority-note">
        Status, elegibilidade e alertas vêm do snapshot do serviço; os filtros
        não recalculam nem alteram estado.
      </p>

      {filteredRows.length === 0 ? (
        <div className="page-empty compact task-results-empty" id="task-results">
          <h3>Nenhuma task encontrada</h3>
          <p>
            Ajuste os filtros para consultar outra parte da projeção
            operacional.
          </p>
        </div>
      ) : (
        <div className="tasks-table-wrap" id="task-results">
          <table className="tasks-table">
            <caption className="sr-only">
              Tasks do snapshot operacional filtradas
            </caption>
            <thead>
              <tr>
                <th>Task</th>
                <th>Status</th>
                <th>Perfil</th>
                <th>Risco</th>
                <th>Prioridade</th>
                <th>Atenção</th>
              </tr>
            </thead>
            <tbody>
              {filteredRows.map(({ task, column, attention }) => {
                const status = displayStatus(task, column);
                const profile = taskProfile(task);
                return (
                  <tr key={task.id}>
                    <td>
                      <button
                        type="button"
                        className="task-title-button"
                        onClick={() => onOpenTask(task)}
                        aria-label={`Abrir detalhes de ${taskTitle(task)}`}
                      >
                        <strong>{taskTitle(task)}</strong>
                        <span>{task.id}</span>
                      </button>
                    </td>
                    <td>
                      <span
                        className={`task-status ${statusTone(status)}`}
                        title={`Projeção na coluna ${columnLabels[column]}`}
                      >
                        {status}
                      </span>
                      <small>{columnLabels[column]}</small>
                    </td>
                    <td>{profile || <span className="task-muted">Sem perfil</span>}</td>
                    <td>
                      <span
                        className={`risk ${(task.risk || "LOW").toLowerCase()}`}
                      >
                        {task.risk || "LOW"}
                      </span>
                    </td>
                    <td>
                      <span
                        className={`priority ${(task.priority || "P2").toLowerCase()}`}
                      >
                        {task.priority || "P2"}
                      </span>
                    </td>
                    <td>
                      {attention.length === 0 ? (
                        <span className="task-muted">Sem alerta</span>
                      ) : (
                        <div className="task-attention-tags">
                          {attention.map((key) => (
                            <span
                              className={`attention-tag ${attentionLabels[key].tone}`}
                              key={key}
                            >
                              {attentionLabels[key].label}
                            </span>
                          ))}
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
