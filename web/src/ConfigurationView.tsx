import { useEffect, useMemo, useState } from "react";
import {
  applyConfigDraft,
  createIdempotencyKey,
  createConfigDraft,
  getConfigState,
  requestHumanAuthorization,
  validateConfigDraft,
  type CommandResult,
  type ConfigRevision,
  type ConfigValidation,
} from "./api";

interface ConfigurationViewProps {
  onApplied: () => void | Promise<void>;
}

interface DiffLine {
  kind: "context" | "add" | "remove";
  text: string;
  oldNumber?: number;
  newNumber?: number;
}

function prettyContent(config: ConfigRevision | null): string {
  return config ? JSON.stringify(config.content, null, 2) : "";
}

function messageFrom(reason: unknown): string {
  return reason instanceof Error
    ? reason.message
    : "A operação de configuração não foi concluída.";
}

function buildDiff(before: string, after: string): DiffLine[] {
  const oldLines = before ? before.split(/\r?\n/) : [];
  const newLines = after ? after.split(/\r?\n/) : [];

  if (oldLines.length * newLines.length > 100_000) {
    return [
      ...oldLines.map((text, index) => ({
        kind: "remove" as const,
        text,
        oldNumber: index + 1,
      })),
      ...newLines.map((text, index) => ({
        kind: "add" as const,
        text,
        newNumber: index + 1,
      })),
    ];
  }

  const lengths = Array.from(
    { length: oldLines.length + 1 },
    () => new Uint16Array(newLines.length + 1),
  );
  for (let oldIndex = oldLines.length - 1; oldIndex >= 0; oldIndex -= 1) {
    for (let newIndex = newLines.length - 1; newIndex >= 0; newIndex -= 1) {
      lengths[oldIndex][newIndex] =
        oldLines[oldIndex] === newLines[newIndex]
          ? lengths[oldIndex + 1][newIndex + 1] + 1
          : Math.max(lengths[oldIndex + 1][newIndex], lengths[oldIndex][newIndex + 1]);
    }
  }

  const result: DiffLine[] = [];
  let oldIndex = 0;
  let newIndex = 0;
  while (oldIndex < oldLines.length && newIndex < newLines.length) {
    if (oldLines[oldIndex] === newLines[newIndex]) {
      result.push({
        kind: "context",
        text: oldLines[oldIndex],
        oldNumber: oldIndex + 1,
        newNumber: newIndex + 1,
      });
      oldIndex += 1;
      newIndex += 1;
    } else if (lengths[oldIndex + 1][newIndex] >= lengths[oldIndex][newIndex + 1]) {
      result.push({
        kind: "remove",
        text: oldLines[oldIndex],
        oldNumber: oldIndex + 1,
      });
      oldIndex += 1;
    } else {
      result.push({
        kind: "add",
        text: newLines[newIndex],
        newNumber: newIndex + 1,
      });
      newIndex += 1;
    }
  }
  while (oldIndex < oldLines.length) {
    result.push({
      kind: "remove",
      text: oldLines[oldIndex],
      oldNumber: oldIndex + 1,
    });
    oldIndex += 1;
  }
  while (newIndex < newLines.length) {
    result.push({
      kind: "add",
      text: newLines[newIndex],
      newNumber: newIndex + 1,
    });
    newIndex += 1;
  }
  return result;
}

export function ConfigurationView({ onApplied }: ConfigurationViewProps) {
  const [config, setConfig] = useState<ConfigRevision | null>(null);
  const [editor, setEditor] = useState("");
  const [latestVersion, setLatestVersion] = useState(0);
  const [draft, setDraft] = useState<CommandResult | null>(null);
  const [validation, setValidation] = useState<ConfigValidation | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [applied, setApplied] = useState(false);
  const [draftRequestKey, setDraftRequestKey] = useState<string | null>(null);
  const [applyRequestKey, setApplyRequestKey] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<"draft" | "validate" | "apply" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    void getConfigState(controller.signal)
      .then((state) => {
        const pendingDraft =
          state.latest?.status === "DRAFT" ? state.latest : null;
        setConfig(state.active);
        setEditor(prettyContent(pendingDraft ?? state.active));
        setLatestVersion(state.latest?.version ?? 0);
        setDraft(
          pendingDraft
            ? {
                entity_id: pendingDraft.id,
                version: pendingDraft.version,
                state: pendingDraft.status,
                replayed: false,
                data: {
                  revision: pendingDraft.revision,
                  content_hash: pendingDraft.content_hash,
                },
              }
            : null,
        );
        setValidation(null);
        setConfirmed(false);
        setApplied(false);
        setDraftRequestKey(null);
        setApplyRequestKey(null);
        setError(null);
        setNotice(
          pendingDraft
            ? `Draft ${pendingDraft.id} retomado do servidor. Valide-o antes de aplicar.`
            : null,
        );
      })
      .catch((reason) => {
        if (reason instanceof DOMException && reason.name === "AbortError") return;
        setError(messageFrom(reason));
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [reload]);

  const activeText = useMemo(() => prettyContent(config), [config]);
  const reviewText = useMemo(
    () => validation ? JSON.stringify(validation.content, null, 2) : editor,
    [editor, validation],
  );
  const diff = useMemo(
    () => buildDiff(activeText, reviewText),
    [activeText, reviewText],
  );
  const changedLineCount = diff.filter((line) => line.kind !== "context").length;
  const hasChanges = editor.trim().length > 0 && editor !== activeText;

  const updateEditor = (value: string) => {
    setEditor(value);
    if (draft || validation || applied || draftRequestKey || applyRequestKey) {
      setDraft(null);
      setValidation(null);
      setConfirmed(false);
      setApplied(false);
      setDraftRequestKey(null);
      setApplyRequestKey(null);
      setNotice("O texto mudou. Crie um novo draft para obter outra revisão auditável.");
    }
  };

  const createDraft = async () => {
    setBusy("draft");
    setError(null);
    setNotice(null);
    try {
      const requestKey =
        draftRequestKey ?? createIdempotencyKey("config-draft");
      setDraftRequestKey(requestKey);
      const result = await createConfigDraft(editor, latestVersion, requestKey);
      setDraft(result);
      setLatestVersion(result.version);
      setValidation(null);
      setConfirmed(false);
      setApplied(false);
      setApplyRequestKey(null);
      setNotice(`Draft ${result.entity_id} criado. Valide-o antes de revisar o diff.`);
    } catch (reason) {
      setError(messageFrom(reason));
    } finally {
      setBusy(null);
    }
  };

  const validateDraft = async () => {
    if (!draft) return;
    setBusy("validate");
    setError(null);
    setNotice(null);
    try {
      const result = await validateConfigDraft(draft.entity_id);
      setValidation(result);
      setConfirmed(false);
      setApplyRequestKey(null);
      setNotice("Validação concluída pelo servidor. Revise as linhas alteradas.");
    } catch (reason) {
      setError(messageFrom(reason));
    } finally {
      setBusy(null);
    }
  };

  const applyDraft = async () => {
    if (!validation || !confirmed) return;
    setBusy("apply");
    setError(null);
    setNotice(null);
    try {
      const requestKey =
        applyRequestKey ?? createIdempotencyKey("config-apply");
      setApplyRequestKey(requestKey);
      const authorization = await requestHumanAuthorization(
        "config_apply_draft",
        validation.draft_id,
      );
      const result = await applyConfigDraft(
        validation,
        authorization.capability_token,
        requestKey,
      );
      const state = await getConfigState();
      setConfig(state.active);
      setEditor(prettyContent(state.active));
      setLatestVersion(result.version);
      setApplied(true);
      setConfirmed(false);
      setNotice(
        `Revisão ${state.active?.revision ?? validation.revision} aplicada pelo serviço e registrada como ${result.state}.`,
      );
      await onApplied();
    } catch (reason) {
      setError(messageFrom(reason));
    } finally {
      setBusy(null);
    }
  };

  const stageState = (stage: "draft" | "validate" | "diff" | "apply") => {
    if (stage === "draft") return draft ? "complete" : "current";
    if (stage === "validate") return validation?.valid ? "complete" : draft ? "current" : "";
    if (stage === "diff") return validation?.valid ? "complete" : "";
    return applied ? "complete" : validation?.valid ? "current" : "";
  };

  return (
    <section className="config-view" aria-labelledby="config-title">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Política versionada</p>
          <h2 id="config-title">Configuração</h2>
        </div>
        <button
          className="secondary-button"
          onClick={() => {
            setLoading(true);
            setReload((value) => value + 1);
          }}
        >
          Recarregar do servidor
        </button>
      </div>

      <ol className="workflow-steps" aria-label="Fluxo seguro de configuração">
        <li className={stageState("draft")}><span>1</span><div><strong>Draft</strong><small>Criação auditável</small></div></li>
        <li className={stageState("validate")}><span>2</span><div><strong>Validar</strong><small>Política no servidor</small></div></li>
        <li className={stageState("diff")}><span>3</span><div><strong>Diff</strong><small>Revisão humana</small></div></li>
        <li className={stageState("apply")}><span>4</span><div><strong>Aplicar</strong><small>Autorização explícita</small></div></li>
      </ol>

      {error && (
        <div className="inline-message error" role="alert">
          <strong>Operação recusada</strong><span>{error}</span>
        </div>
      )}
      {notice && (
        <div className="inline-message success" role="status">
          <strong>Configuração</strong><span>{notice}</span>
        </div>
      )}

      <article className="config-current">
        <div>
          <span className={`status-chip ${(config?.status || "empty").toLowerCase()}`}>
            {config?.status === "ACTIVE" ? "Ativa" : config ? "Última revisão" : "Sem revisão"}
          </span>
          <h3>
            {config
              ? `Revisão ${config.revision} · versão ${config.version}`
              : "Nenhuma configuração operacional registrada"}
          </h3>
          <p>
            {config
              ? `Fingerprint ${config.content_hash}`
              : "Cole uma configuração YAML completa para iniciar o fluxo."}
          </p>
        </div>
        <dl>
          <div><dt>Criada</dt><dd>{config?.created_at || "—"}</dd></div>
          <div><dt>Aplicada</dt><dd>{config?.applied_at || "—"}</dd></div>
        </dl>
      </article>
      <details className="config-source">
        <summary>
          Ver conteúdo {config?.status === "ACTIVE" ? "ativo" : "da revisão mais recente"}
        </summary>
        <pre>{activeText || "Nenhum conteúdo operacional foi registrado."}</pre>
      </details>

      <div className="config-layout">
        <section className="editor-panel" aria-labelledby="draft-editor-title">
          <div className="subsection-heading">
            <div>
              <h3 id="draft-editor-title">Conteúdo do draft</h3>
              <p>JSON formatado também é YAML válido. O servidor valida e canonicaliza o conteúdo.</p>
            </div>
            <span>{editor.split(/\r?\n/).length} linhas</span>
          </div>
          {loading ? (
            <div className="panel-loading" role="status">Carregando configuração…</div>
          ) : (
            <textarea
              value={editor}
              onChange={(event) => updateEditor(event.target.value)}
              aria-label="Conteúdo YAML do draft de configuração"
              placeholder="schema_version: 1&#10;project:&#10;  name: Meu projeto"
              spellCheck={false}
            />
          )}
          <div className="panel-actions">
            <span>
              {draft
                ? `Draft ${draft.entity_id} · versão ${draft.version}`
                : `Próxima mutação espera a versão ${latestVersion}`}
            </span>
            <button
              className="primary-button"
              disabled={!hasChanges || draft !== null || applied || busy !== null}
              onClick={() => void createDraft()}
            >
              {busy === "draft" ? "Criando…" : "Criar draft"}
            </button>
          </div>
        </section>

        <section className="review-panel" aria-labelledby="config-review-title">
          <div className="subsection-heading">
            <div>
              <h3 id="config-review-title">Validação e diff</h3>
              <p>A aplicação permanece bloqueada até o servidor validar esta revisão.</p>
            </div>
            <span>{validation?.valid ? `${changedLineCount} alterações` : "Pendente"}</span>
          </div>

          {!draft ? (
            <div className="review-placeholder">
              <span>1</span>
              <p>Edite o conteúdo e crie um draft. Nenhuma alteração local muda a política ativa.</p>
            </div>
          ) : !validation ? (
            <div className="review-placeholder ready">
              <span>2</span>
              <p>O draft existe no servidor. Execute a validação explícita para liberar o diff.</p>
              <button
                className="primary-button"
                disabled={busy !== null}
                onClick={() => void validateDraft()}
              >
                {busy === "validate" ? "Validando…" : "Validar no servidor"}
              </button>
            </div>
          ) : (
            <>
              <div className="validation-summary">
                <span className="validation-mark" aria-hidden="true">✓</span>
                <div>
                  <strong>Draft válido · revisão {validation.revision}</strong>
                  <p>Fingerprint confirmado: <code>{validation.content_hash}</code></p>
                </div>
              </div>
              <p className="diff-caption">
                O diff compara a revisão exibida ao texto submetido. O fingerprint acima identifica
                a forma canônica validada e aplicada pelo servidor.
              </p>
              <div className="config-diff" role="region" aria-label="Diff da configuração" tabIndex={0}>
                {diff.length === 0 ? (
                  <p className="diff-empty">Nenhuma diferença em relação à revisão exibida.</p>
                ) : (
                  diff.map((line, index) => (
                    <div className={`diff-line ${line.kind}`} key={`${index}-${line.kind}`}>
                      <span>{line.oldNumber ?? ""}</span>
                      <span>{line.newNumber ?? ""}</span>
                      <b>{line.kind === "add" ? "+" : line.kind === "remove" ? "−" : " "}</b>
                      <code>{line.text || " "}</code>
                    </div>
                  ))
                )}
              </div>
              <label className="apply-confirmation">
                <input
                  type="checkbox"
                  checked={confirmed}
                  onChange={(event) => setConfirmed(event.target.checked)}
                  disabled={applied || busy !== null}
                />
                <span>
                  Revisei o diff do conteúdo submetido e autorizo aplicar o draft identificado pelo fingerprint validado.
                  <small>O navegador solicitará uma autorização humana curta ao servidor.</small>
                </span>
              </label>
              <div className="panel-actions apply">
                <span>Sem ator digitado: identidade vem da sessão local.</span>
                <button
                  className="danger-button"
                  disabled={!confirmed || applied || busy !== null}
                  onClick={() => void applyDraft()}
                >
                  {busy === "apply" ? "Autorizando e aplicando…" : applied ? "Aplicada" : "Autorizar e aplicar"}
                </button>
              </div>
            </>
          )}
        </section>
      </div>
    </section>
  );
}
