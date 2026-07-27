import { useState, type FormEvent } from "react";
import {
  cancelTask,
  createIdempotencyKey,
  requestHumanAuthorization,
  type Task,
} from "./api";

interface TaskActionPanelProps {
  task: Task;
  onCanceled: (task: Task) => void | Promise<void>;
}

function messageFrom(reason: unknown): string {
  return reason instanceof Error ? reason.message : "O servidor recusou a ação.";
}

export function TaskActionPanel({ task, onCanceled }: TaskActionPanelProps) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [complete, setComplete] = useState(false);
  const [requestKey, setRequestKey] = useState<string | null>(null);
  const phase = (task.state || task.phase || task.status || "").toUpperCase();
  const terminal = phase === "DONE" || phase === "CANCELED";

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const trimmedReason = reason.trim();
    if (!trimmedReason || terminal) return;
    setBusy(true);
    setError(null);
    try {
      const key = requestKey ?? createIdempotencyKey("task-cancel");
      setRequestKey(key);
      const authorization = await requestHumanAuthorization("task_cancel", task.id);
      const result = await cancelTask(
        task,
        trimmedReason,
        authorization.capability_token,
        key,
      );
      const updated = {
        ...task,
        state: result.state,
        phase: result.state,
        status: result.state,
        version: result.version,
      };
      setComplete(true);
      await onCanceled(updated);
    } catch (reasonCaught) {
      setError(messageFrom(reasonCaught));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="task-actions" aria-labelledby="task-actions-title">
      <div>
        <h3 id="task-actions-title">Ações da task</h3>
        <p>
          Não há drag-and-drop: cada comando explícito é validado, versionado e auditado pelo servidor.
        </p>
      </div>
      {terminal || complete ? (
        <div className="task-action-state" role="status">
          <strong>{phase === "DONE" ? "Task concluída" : "Task cancelada"}</strong>
          <span>Nenhuma ação de cancelamento está disponível neste estado.</span>
        </div>
      ) : !open ? (
        <button className="secondary-button destructive" onClick={() => setOpen(true)}>
          Cancelar task…
        </button>
      ) : (
        <form onSubmit={(event) => void submit(event)}>
          <label htmlFor={`cancel-reason-${task.id}`}>Motivo do cancelamento</label>
          <textarea
            id={`cancel-reason-${task.id}`}
            value={reason}
            onChange={(event) => {
              setReason(event.target.value);
              setRequestKey(null);
            }}
            placeholder="Explique por que esta task deve ser cancelada."
            minLength={1}
            maxLength={4000}
            required
            autoFocus
          />
          {error && <p className="form-error" role="alert">{error}</p>}
          <p className="form-note">
            “Autorizar e cancelar” solicita uma capacidade humana curta vinculada somente a {task.id}.
          </p>
          <div className="form-actions">
            <button
              type="button"
              className="secondary-button"
              disabled={busy}
              onClick={() => {
                setOpen(false);
                setError(null);
              }}
            >
              Voltar
            </button>
            <button
              type="submit"
              className="danger-button"
              disabled={busy || reason.trim().length === 0 || task.version === undefined}
            >
              {busy ? "Autorizando…" : "Autorizar e cancelar"}
            </button>
          </div>
        </form>
      )}
    </section>
  );
}
