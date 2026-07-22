# AgentBoard v1 — controle local de execução multiagente

**Status:** Aprovado  
**Data de aprovação:** 2026-07-22  
**Base SHA:** `3eedda66be90d49cb9d7e2910c875a990a5f731f`

## Resumo

Transformar o scaffold atual em um control plane completo, local-first e orientado ao Codex. As referências em `docs/ref` serão usadas apenas como inspiração visual e operacional.

O fluxo será:

`requisitos → plano em rascunho → aprovação humana → scheduler → reserva → spawn nativo do Codex → run → checkpoints → revisão → integração Git local → DONE`

O primeiro lançamento incluirá domínio, SQLite, MCP, API/SSE, dashboard, configuração, worktrees Git locais, plugin, hooks e distribuição para Windows, macOS e Linux. Não haverá GitHub, push, pull, fetch, PR, merge remoto, deploy ou qualquer integração remota.

## Modelo e contratos

### Domínio autoritativo

| Conceito | Estados principais | Regra |
|---|---|---|
| Plano | `DRAFT`, `ACTIVE`, `SUPERSEDED`, `COMPLETED`, `CANCELED` | Apenas aprovação materializa tasks executáveis. |
| Task | `BACKLOG`, `IN_PROGRESS`, `VERIFYING`, `DONE`, `CANCELED` | Somente o serviço altera a fase. |
| Assignment | `RESERVED`, `ACCEPTED`, `EXPIRED`, `RELEASED` | Reserva agente, capacidade e caminhos antes do spawn. |
| Run | `RUNNING`, `SUCCEEDED`, `FAILED`, `BLOCKED`, `STALE`, `CANCELED` | Representa uma tentativa, nunca a task inteira. |
| Review | `PENDING`, `APPROVED`, `CHANGES_REQUESTED` | Toda task recebe decisão proporcional ao risco. |
| Blocker | `OPEN`, `RESOLVED`, `WAIVED` | Preserva motivo, responsável, fase anterior e retomada. |

- `READY` será uma projeção calculada, não uma fase persistida.
- `ASSIGNED` será representado pelo assignment ativo.
- `BLOCKED` será uma condição com registro próprio.
- `REWORK` será uma decisão `CHANGES_REQUESTED` que devolve a task ao `BACKLOG`.
- Uma task bloqueada, stale ou em rework encerra o run, libera lease/WIP e conserva checkpoints.
- `IN_PROGRESS` exige assignment aceito, run ativo e lease não expirado.
- `VERIFYING` mantém WIP e o lease de arquivos até aprovação ou rework.
- `DONE` exige evidência, revisão e integração local quando o perfil da task exigir Git.

A configuração antiga de nove estados será substituída por esse modelo. Como o schema atual não persiste tasks, não há dados operacionais a converter; documentação e exemplo YAML receberão um mapa explícito de compatibilidade.

### Planejamento e scheduler

- O plano possui revisões imutáveis, grupos opcionais — milestone, gate ou feature — e tasks executáveis.
- Cada task registra objetivo, escopo, aceite, testes, prioridade `P0–P3`, risco, perfil sugerido, caminhos previstos e dependências.
- Dependências serão `REQUIRES` ou `ORDER_AFTER`. Todas as `REQUIRES` usam semântica “all”; relações `any` e tipos personalizados ficam fora da v1.
- Dependência cancelada continua bloqueando até uma dispensa aprovada e vinculada à aresta exata.
- O serviço calcula elegibilidade e motivos de impedimento; o scheduler acrescenta WIP, capacidade, agentes e conflito de leases.
- A ordenação será determinística: prioridade, caminho crítico, quantidade de trabalho desbloqueado, ordem do plano e ID.
- Reservations, `IN_PROGRESS` e `VERIFYING` consomem WIP.
- Alterações durante a execução criam nova revisão e análise de impacto. Runs ativos permanecem vinculados às revisões antigas.
- Emendas dentro do escopo podem ser aprovadas pelo orquestrador; expansão de objetivo, risco ou permissões volta ao usuário.
- Configurações novas valem no momento linearizável do próximo claim. Retry é um novo claim; heartbeat e renovação são continuação do run existente.

### Autoridade, concorrência e persistência

- Toda mutação terá `expected_version`, `idempotency_key` e ator derivado de credencial, nunca confiado a texto enviado pelo agente.
- Capacidades temporárias serão armazenadas por hash e vinculadas a projeto, task, run, instância, lease generation, operações permitidas e expiração.
- Um worker só inicia, atualiza, bloqueia ou encerra seu próprio run; reviewer não pode revisar o próprio trabalho.
- Risco baixo/médio permite revisão do orquestrador; alto exige reviewer independente; crítico exige reviewer e aprovação humana.
- Cada lease recebe uma geração. Heartbeats ou resultados de uma geração expirada serão rejeitados mesmo após reclaim.
- Claims executarão `BEGIN IMMEDIATE` e validarão atomicamente versão, dependências, WIP, revisão de política, perfil, capacidade e sobreposição de caminhos.
- Idempotência armazenará hash do request e resultado: repetição idêntica devolve o resultado anterior; mesma chave com payload diferente gera conflito.
- SQLite terá tabelas agrupadas para planos/tasks, dependências/dispensas, agents/assignments/leases, runs/checkpoints, blockers/reviews/evidências, configurações, capacidades, idempotência, Git e event log.
- Migrações serão numeradas, transacionais e precedidas por backup SQLite local. Falha reverte a transação; downgrade destrutivo não será automatizado.

### Interfaces públicas e runtime

MCP exporá ferramentas de workflow, não CRUD genérico:

- Leitura: `project_open`, `plan_get`, `board_snapshot`, `task_get`, `task_list`, `schedule_next`, `agent_list`, `run_get`, `run_list`, `event_list`, `config_get`.
- Planejamento: `plan_draft_create`, `plan_draft_update`, `plan_validate`, `plan_approve`, `plan_revision_create`, `plan_revision_approve`.
- Execução: `task_claim`, `run_start`, `task_heartbeat`, `task_block`, `task_report_result`, `run_fail`, `review_claim`, `review_start`, `review_decide`, `task_cancel`, `dependency_waive`.
- Configuração/Git: `config_draft_create`, `config_validate`, `config_apply_draft`, `git_checkpoint`, `git_integrate`, `dashboard_open`.

A API HTTP em `/api/v1` usará os mesmos DTOs e códigos de erro do MCP. SSE em `/api/v1/events` suportará `Last-Event-ID`, replay e ressincronização por snapshot.

O runtime será único por projeto:

- `agentboard runtime` possuirá SQLite, serviço, HTTP e SSE.
- `agentboard mcp` será uma ponte stdio que localiza ou inicia o runtime e se conecta por loopback autenticado.
- Identidade do projeto será derivada do caminho real e do Git common directory.
- Lock, PID, nonce e porta ficarão em `.agentboard/`, nunca no Git.
- Dois starters concorrentes convergirão para o mesmo runtime; locks abandonados terão recuperação validada.
- O runtime encerrará após o último cliente MCP sair e um período curto de tolerância.
- Dashboard usará bootstrap de uso único, cookie `HttpOnly/SameSite`, validação de Host/Origin e proteção CSRF.

O Codex continuará responsável por criar e controlar subagentes nativamente. A skill AgentBoard selecionará a task, fará o claim, chamará o spawn do Codex e vinculará a thread resultante ao run.

## Implementação incremental

1. **Contratos e testes de caracterização**
   - Atualizar arquitetura, configuração, máquina de fases, papéis e contratos MCP.
   - Fixar schemas Pydantic, códigos de erro e matriz de comandos por papel.

2. **SQLite e serviço de domínio**
   - Criar migrations, repositórios, transações imediatas, versões, idempotência e event log.
   - Implementar fases, dependências, readiness, WIP, leases e fencing.

3. **Planos, scheduler e execução**
   - Implementar drafts, aprovação, revisão de planos, grupos, análise de impacto e ranking.
   - Adicionar instances, assignments, runs, checkpoints, blockers, retries e reviews.
   - Retry automático apenas para falhas transitórias, com limite padrão de duas tentativas; falhas lógicas, testes, conflitos, autorização e cancelamentos exigem decisão.

4. **Runtime, MCP, HTTP e SSE**
   - Introduzir o singleton por projeto e a ponte MCP stdio.
   - Expor ferramentas e endpoints finos sobre o mesmo serviço.
   - Implementar autenticação local por capacidades, replay SSE e recuperação após crash.

5. **Dashboard e configuração**
   - Criar views de Planos, Quadro, Tasks, Agentes, Execuções e Configuração.
   - Usar cinco colunas: Backlog, Elegíveis, Em execução, Verificação e Concluídas.
   - Exibir bloqueios, rework, lease expirado, conflito e evidência ausente em uma faixa de atenção.
   - Incorporar busca, filtros, métricas clicáveis, dependências/dependentes, drawer rápido e detalhe completo.
   - Mostrar checkpoints objetivos, nunca percentual canônico.
   - Configuração seguirá rascunho → validação → diff → aplicação explícita.

6. **Git estritamente local**
   - Criar uma branch de integração local por plano e worktrees isolados por run.
   - Gerar checkpoints por commits locais e integrar tasks aprovadas na branch do plano.
   - Integrar o plano na branch-alvo apenas com autorização, target SHA esperado e árvore limpa.
   - Recusar conflitos, target movido, arquivos fora do lease e repositório inseguro; nunca executar reset destrutivo ou stash automático.
   - Desabilitar hooks Git nos subprocessos do adapter e recusar filtros externos, LFS ou submódulos que possam iniciar rede.
   - Remover qualquer dependência de GitHub e bloquear comandos remotos no adapter.
   - Empacotar hook Codex `PreToolUse` para impedir tentativas comuns de Git remoto e Git write fora do MCP. O dashboard recusará orquestração autônoma quando a proteção esperada não estiver ativa.

7. **Plugin e distribuição**
   - Atualizar manifesto, skill, perfis e `.mcp.json`; o runtime instalado será chamado diretamente, sem `uvx` baixar pacotes durante o uso.
   - Preparar wheel/CLI, marketplace local, documentação de instalação, bootstrap e recuperação.
   - Validar Windows, macOS e Ubuntu com Python ≥3.11 e Node LTS.
   - Publicação de pacotes ou repositórios será uma ação humana externa ao AgentBoard.

## Testes e aceite

- Matriz completa de fases, condições, papéis, risco, evidências e migração semântica.
- Claims simultâneos no limite de WIP, leases sobrepostos e versões concorrentes.
- Falha entre reservation e spawn, heartbeat versus expiração e rejeição de worker antigo após reclaim.
- Cancelamento/waiver/claim concorrentes; ciclos e isolamento entre dependências obrigatórias e de ordenação.
- Apply de configuração versus claim, retry sob nova revisão e imutabilidade de runs existentes.
- Capability expirada, revogada, fora do escopo, self-review, downgrade de risco e aprovação crítica sem humano.
- Dois runtimes iniciados simultaneamente, lock abandonado, crash SQLite e reconexão SSE.
- Host/Origin/CSRF inválidos, segredo em logs e browser hostil acessando localhost.
- Worktrees paralelos, commits restritos ao lease, conflito local, target SHA alterado e árvore-alvo suja.
- Testes negativos comprovando que push, pull, fetch, clone, `ls-remote`, `gh`, hooks, filtros, LFS e submódulos remotos não são executados.
- UI: teclado, foco, drag como intenção validada pelo servidor, responsividade, loading/empty/error, atenção e conflitos otimistas.
- E2E: aprovar plano, executar duas tasks independentes em paralelo, registrar checkpoints, revisar por risco, integrar localmente e concluir sem qualquer acesso de rede.
- Gates finais: `uv run pytest`, `uv run ruff check .`, testes web, `npm run lint` e `npm run build` nas três plataformas.

## Premissas

- `docs/ref` permanece apenas como referência e não será importado como estado.
- `agentboard.yaml` será a configuração versionada do projeto; `.codex/agents/*.toml` continuará sendo a fonte dos perfis.
- `.agentboard/` conterá somente banco, locks, backups e runtime local ignorados pelo Git.
- Não haverá cloud, multi-tenant, listener público, telemetria, GitHub ou operação remota.
- Hooks Codex são defesa adicional; a garantia principal de ausência de rede pertence ao adapter, aos perfis restritos e aos testes negativos.
- O primeiro release público só será considerado concluído quando todas as sete etapas e o cenário E2E multiplataforma estiverem aprovados.
