import { useEffect, useMemo, useState } from "react";
import {
  approvePlan,
  createIdempotencyKey,
  getPlan,
  getPlans,
  planApprovalResource,
  requestHumanAuthorization,
  validatePlan,
  type Plan,
  type PlanRevision,
  type PlanValidation,
} from "./api";

interface PlansViewProps {
  onApproved: () => void | Promise<void>;
}

function planKey(plan: Plan): string {
  return `${plan.id}:${plan.revision}`;
}

function formatDate(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return value;
  return new Intl.DateTimeFormat("pt-BR", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function messageFrom(reason: unknown): string {
  return reason instanceof Error ? reason.message : "Não foi possível carregar os planos.";
}

export function PlansView({ onApproved }: PlansViewProps) {
  const [plans, setPlans] = useState<Plan[]>([]);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [detail, setDetail] = useState<PlanRevision | null>(null);
  const [validation, setValidation] = useState<PlanValidation | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [approvalRequestKey, setApprovalRequestKey] = useState<string | null>(null);
  const [approving, setApproving] = useState(false);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    void getPlans(controller.signal)
      .then((items) => {
        setPlans(items);
        setSelectedKey((current) => {
          if (current && items.some((item) => planKey(item) === current)) return current;
          const active = items.find((item) => item.status === "ACTIVE");
          return active ? planKey(active) : items[0] ? planKey(items[0]) : null;
        });
        setError(null);
        setConfirmed(false);
        setApprovalRequestKey(null);
      })
      .catch((reason) => {
        if (reason instanceof DOMException && reason.name === "AbortError") return;
        setError(messageFrom(reason));
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [reload]);

  const selected = useMemo(
    () => plans.find((item) => planKey(item) === selectedKey) ?? null,
    [plans, selectedKey],
  );

  useEffect(() => {
    const controller = new AbortController();
    if (!selected) {
      queueMicrotask(() => {
        if (controller.signal.aborted) return;
        setDetail(null);
        setValidation(null);
        setDetailLoading(false);
      });
      return () => controller.abort();
    }
    queueMicrotask(() => {
      if (controller.signal.aborted) return;
      setDetailLoading(true);
      setDetail(null);
      setValidation(null);
      void Promise.all([
        getPlan(selected.id, selected.revision, controller.signal),
        validatePlan(selected.id, selected.revision, controller.signal),
      ])
        .then(([nextDetail, nextValidation]) => {
          setDetail(nextDetail);
          setValidation(nextValidation);
          setError(null);
        })
        .catch((reason) => {
          if (reason instanceof DOMException && reason.name === "AbortError") return;
          setError(messageFrom(reason));
        })
        .finally(() => setDetailLoading(false));
    });
    return () => controller.abort();
  }, [selected]);

  const active = plans.find((plan) => plan.status === "ACTIVE") ?? null;
  const objective =
    typeof detail?.content.objective === "string" ? detail.content.objective : null;
  const tasks = Array.isArray(detail?.content.tasks) ? detail.content.tasks : [];
  const metrics = validation?.graph_metrics
    ? Object.entries(validation.graph_metrics)
    : [];
  const canApprove = Boolean(
    detail &&
      validation?.valid &&
      detail.status === "DRAFT" &&
      detail.version !== undefined &&
      confirmed &&
      !detailLoading &&
      !approving,
  );

  const approveSelected = async () => {
    if (!detail || !canApprove || detail.version === undefined) return;
    setApproving(true);
    setError(null);
    setNotice(null);
    try {
      const idempotencyKey =
        approvalRequestKey ?? createIdempotencyKey("plan-approve");
      setApprovalRequestKey(idempotencyKey);
      const authorization = await requestHumanAuthorization(
        "plan_approve",
        planApprovalResource(detail.id, detail.revision, detail.version),
      );
      const result = await approvePlan(
        detail,
        authorization.capability_token,
        idempotencyKey,
      );
      setConfirmed(false);
      setApprovalRequestKey(null);
      setNotice(`Revisão aprovada e ativada como ${result.state}.`);
      setReload((value) => value + 1);
      await onApproved();
    } catch (reason) {
      setError(messageFrom(reason));
    } finally {
      setApproving(false);
    }
  };

  return (
    <section className="plans-view" aria-labelledby="plans-title">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Revisões imutáveis</p>
          <h2 id="plans-title">Planos</h2>
        </div>
        <button
          className="secondary-button"
          onClick={() => {
            setLoading(true);
            setReload((value) => value + 1);
          }}
        >
          Atualizar
        </button>
      </div>

      {error && (
        <div className="inline-message error" role="alert">
          <strong>Planos indisponíveis</strong>
          <span>{error}</span>
        </div>
      )}
      {notice && (
        <div className="inline-message success" role="status">
          <strong>Plano</strong>
          <span>{notice}</span>
        </div>
      )}

      <article className="active-plan-card">
        <div>
          <span className="status-chip active">Plano ativo</span>
          <h3>{active?.title || active?.id || "Nenhum plano aprovado"}</h3>
          <p>
            {active
              ? `${active.id} · revisão ${active.revision} · versão ${active.version ?? "—"}`
              : "Tasks executáveis só aparecem depois da aprovação humana de uma revisão."}
          </p>
        </div>
        {active?.approved_at && (
          <dl>
            <dt>Aprovado em</dt>
            <dd>{formatDate(active.approved_at)}</dd>
          </dl>
        )}
      </article>

      {loading && plans.length === 0 ? (
        <div className="panel-loading" role="status">Carregando revisões…</div>
      ) : plans.length === 0 ? (
        <div className="page-empty compact">
          <h3>Nenhuma revisão registrada</h3>
          <p>Drafts e revisões aprovadas aparecerão aqui sem transformar o navegador em fonte canônica.</p>
        </div>
      ) : (
        <div className="plan-layout">
          <div className="revision-list" aria-label="Revisões de planos">
            {plans.map((plan) => {
              const selectedPlan = planKey(plan) === selectedKey;
              return (
                <button
                  className={selectedPlan ? "selected" : ""}
                  key={planKey(plan)}
                  onClick={() => setSelectedKey(planKey(plan))}
                  aria-current={selectedPlan ? "true" : undefined}
                >
                  <span className={`status-chip ${(plan.status || "").toLowerCase()}`}>
                    {plan.status || "UNKNOWN"}
                  </span>
                  <strong>{plan.title || plan.id}</strong>
                  <small>{plan.id} · rev. {plan.revision}</small>
                  <span className="revision-date">{formatDate(plan.created_at)}</span>
                </button>
              );
            })}
          </div>

          <article className="plan-detail" aria-live="polite">
            {detailLoading || !detail ? (
              <div className="panel-loading" role="status">
                {detailLoading ? "Carregando conteúdo da revisão…" : "Selecione uma revisão."}
              </div>
            ) : (
              <>
                <header>
                  <div>
                    <span className={`status-chip ${(detail.status || "").toLowerCase()}`}>
                      {detail.status}
                    </span>
                    <h3>{detail.title || detail.id}</h3>
                    <p>{objective || "Objetivo não informado."}</p>
                  </div>
                  <dl>
                    <div><dt>Revisão</dt><dd>{detail.revision}</dd></div>
                    <div><dt>Versão</dt><dd>{detail.version ?? "—"}</dd></div>
                    <div><dt>Parent</dt><dd>{detail.parent_revision ?? "—"}</dd></div>
                    <div><dt>Validação</dt><dd>{validation?.valid ? "Válida" : "—"}</dd></div>
                  </dl>
                </header>

                <section className="plan-approval" aria-labelledby="plan-approval-title">
                  <div>
                    <h4 id="plan-approval-title">Aprovação humana</h4>
                    <p>
                      Autoriza {detail.id} · revisão {detail.revision} · versão {detail.version ?? "—"}.
                    </p>
                  </div>
                  <label>
                    <input
                      type="checkbox"
                      checked={confirmed}
                      disabled={
                        detail.status !== "DRAFT" ||
                        !validation?.valid ||
                        detail.version === undefined ||
                        detailLoading ||
                        approving
                      }
                      onChange={(event) => setConfirmed(event.target.checked)}
                    />
                    Revisei esta revisão e autorizo sua aprovação.
                  </label>
                  <button
                    className="primary-button"
                    disabled={!canApprove}
                    onClick={() => void approveSelected()}
                  >
                    {approving ? "Aprovando…" : "Autorizar e aprovar"}
                  </button>
                </section>

                {metrics.length > 0 && (
                  <div className="plan-metrics" aria-label="Métricas do grafo">
                    {metrics.map(([taskId, value]) => (
                      <div key={taskId}>
                        <span>{taskId}</span>
                        <strong>{value[0]} caminho · {value[1]} downstream</strong>
                      </div>
                    ))}
                  </div>
                )}

                {validation?.impact && (
                  <aside className="impact-note">
                    <strong>Impacto sobre a revisão {validation.impact.parent_revision}</strong>
                    <span>
                      {(validation.impact.added?.length ?? 0)} adicionadas ·{" "}
                      {(validation.impact.changed?.length ?? 0)} alteradas ·{" "}
                      {(validation.impact.removed?.length ?? 0)} removidas
                    </span>
                    <em>
                      {validation.impact.requires_human_approval
                        ? "Nova aprovação humana necessária"
                        : "Sem expansão sensível detectada"}
                    </em>
                  </aside>
                )}

                <section className="plan-tasks" aria-labelledby="plan-tasks-title">
                  <div className="subsection-heading">
                    <h4 id="plan-tasks-title">Tasks materializadas</h4>
                    <span>{tasks.length}</span>
                  </div>
                  {tasks.length === 0 ? (
                    <p className="detail-muted">Nenhuma task no conteúdo desta revisão.</p>
                  ) : (
                    <ul>
                      {tasks.map((task) => (
                        <li key={task.id}>
                          <div>
                            <span className={`priority ${(task.priority || "P2").toLowerCase()}`}>
                              {task.priority || "P2"}
                            </span>
                            <strong>{task.title || task.objective || task.id}</strong>
                            <small>{task.id} · {task.profile || task.suggested_profile || "perfil aberto"}</small>
                          </div>
                          <span className="logical-task">ID lógico do plano</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </section>
              </>
            )}
          </article>
        </div>
      )}
    </section>
  );
}
