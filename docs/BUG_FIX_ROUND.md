# Rodada consolidada de correções

**Status:** AB-BUG-001 a AB-BUG-005 implementados; itens de robustez permanecem para rodada posterior
**Data do inventário:** 2026-07-30
**Objetivo:** preservar os defeitos encontrados para uma única rodada de implementação,
validação e atualização do plugin.

**Validação executada:** `uv run pytest`, `uv run ruff check .`, `npm run lint`,
`npm run build` e `npm test`.

## Invariantes afetados

- Deve existir exatamente um runtime AgentBoard por identidade de projeto.
- Vários processos MCP do Codex devem compartilhar esse runtime.
- Dashboards de projetos diferentes devem permanecer autenticados simultaneamente.
- Repetições com a mesma chave de idempotência devem continuar válidas após reinício do runtime.
- Um plano somente pode se tornar executável após uma aprovação humana explícita e auditável.
- A inicialização de um projeto deve produzir ou indicar todo o scaffold necessário.

## Resumo

| ID | Gravidade | Área | Problema |
| --- | --- | --- | --- |
| AB-BUG-001 | Crítica | Runtime | Detecção incorreta de PID vivo no Windows quebra o singleton |
| AB-BUG-002 | Alta | Dashboard/Auth | Cookies de runtimes em portas diferentes colidem |
| AB-BUG-003 | Alta | Auth/Idempotência | Capability muda quando o runtime reinicia |
| AB-BUG-004 | Alta | Planos/UI | Não existe ação humana para aprovar um plano no dashboard |
| AB-BUG-005 | Média | Onboarding | `agentboard init` cria somente parte do projeto necessário |

## AB-BUG-001 — Singleton do runtime quebrado no Windows

### Comportamento observado

Mais de um Codex aberto no mesmo projeto iniciou runtimes distintos. Em um snapshot foram
encontrados quatro servidores escutando portas diferentes para a mesma raiz de projeto. Apesar
dos processos ativos, `.agentboard/runtime.json` e `.agentboard/runtime.lock` estavam ausentes.
Também havia 13 arquivos `runtime.lock.stale.*`.

Um runtime separado para outro projeto é esperado. Um runtime adicional para cada Codex ou task
do mesmo projeto não é esperado: cada Codex deve ter sua própria ponte MCP, mas todas as pontes
do mesmo projeto devem compartilhar um único runtime.

### Causa confirmada

`src/agentboard/runtime.py::_pid_is_alive` usa `os.kill(pid, 0)`. No Windows analisado, essa
operação retornou `OSError` com `WinError 87` para PIDs externos que estavam comprovadamente
vivos. O erro é convertido em `False`, fazendo o lock tratar o proprietário ativo como morto.

Depois do período de graça, um concorrente arquiva o lock válido como stale e passa a se
considerar proprietário. O runtime anterior não percebe que perdeu o lock e continua ativo
enquanto seu cliente MCP envia heartbeats.

### Impacto

- Vários `BoardService` e sweepers acessam o mesmo SQLite.
- Metadata pode apontar para um runtime transitório ou desaparecer.
- `agentboard status` pode informar `stopped` com runtimes ainda ativos.
- Cada nova sessão do Codex pode criar outro servidor.
- SSE, sessões e clientes MCP podem ficar distribuídos entre runtimes diferentes.
- A acumulação pode continuar até que os processos Codex correspondentes sejam encerrados.

Não foi observada corrupção do banco, mas o invariante de proprietário único está violado.

### Lacuna de testes

O teste atual de proprietário vivo usa o PID do próprio processo, tratado por um atalho que
retorna `True`. Outro teste substitui a função de liveness por mock. Não existe teste Windows com
um segundo processo real e vivo.

### Critérios de aceitação

- Detectar corretamente um processo externo vivo no Windows.
- Não tomar o lock de um proprietário vivo, mesmo se metadata estiver ausente, inválida ou com
  health check temporariamente indisponível.
- Executar várias chamadas concorrentes a `ensure_runtime` e obter o mesmo PID, porta,
  `project_key` e token de runtime.
- Manter exatamente um `runtime.json` e um `runtime.lock` coerentes com o processo proprietário.
- Fazer um runtime encerrar de forma segura se detectar que não possui mais o lock.
- Cobrir startup lento, retry após o período de graça, PID reutilizado e processo realmente morto.
- Preferir exclusão mútua sustentada pelo sistema operacional ou adicionar uma defesa equivalente
  ao lock baseado apenas em arquivo e PID.

## AB-BUG-002 — Colisão de cookies entre portas

### Comportamento observado

Ao abrir o dashboard de um segundo runtime em `127.0.0.1`, a primeira aba passa a receber erro de
autenticação. O primeiro servidor continua em `LISTENING`; é a credencial da aba que deixa de ser
apresentada corretamente.

### Causa confirmada

Todos os runtimes usam os mesmos nomes:

- `agentboard_session`
- `agentboard_csrf`

Os cookies usam o mesmo host e `Path=/`. Cookies não são isolados por porta. Assim, o bootstrap
do segundo runtime substitui os valores criados pelo primeiro. A aba anterior passa a enviar ao
seu runtime um token que pertence a outra instância e recebe `401`.

Os nomes também estão fixos no frontend, em `web/src/api.ts`.

### Impacto

- Não é possível manter dashboards de dois projetos abertos simultaneamente no mesmo host.
- Reabrir o primeiro dashboard invalida, do ponto de vista do navegador, o segundo.
- Runtimes duplicados do AB-BUG-001 tornam o problema ainda mais frequente.
- Cookies de AgentBoard são enviados a outras portas do mesmo host, o que merece revisão de
  isolamento além da simples correção de nomes.

### Critérios de aceitação

- Isolar a sessão e o CSRF por instância ou identidade de projeto sem depender somente da porta
  para segurança.
- Manter dois dashboards de projetos diferentes autenticados ao mesmo tempo.
- Manter backend e frontend usando exatamente o mesmo namespace de cookie.
- Definir expiração e limpeza para não acumular cookies a cada reinício.
- Testar duas aplicações em portas diferentes com um único cookie jar de navegador.
- Verificar leitura, escrita e erro de CSRF para a instância correta.

## AB-BUG-003 — Capability instável após troca de runtime

### Comportamento confirmado pelo código

Capabilities de worker e reviewer são derivadas por HMAC usando o `api_token` efêmero do runtime.
Esse token muda a cada inicialização.

O hash da capability participa do payload idempotente de `task_claim` e `review_claim`. Uma
repetição legítima, com a mesma chave de idempotência e os mesmos argumentos públicos, produz
outro hash depois de um reinício e pode resultar em `IdempotencyConflictError`.

Excluir apenas o hash do request idempotente não é suficiente: nesse caso, o adapter poderia
devolver uma nova capability enquanto o SQLite preserva o hash da anterior.

### Impacto

- Retry após crash ou reinício pode deixar de ser idempotente.
- Runtimes concorrentes podem derivar credenciais diferentes para o mesmo comando.
- O cliente pode receber uma capability que não corresponde ao hash persistido se a correção for
  feita apenas parcialmente.

### Lacuna de testes

Os testes de replay de `claim_task` não fornecem `capability_token` e não simulam a troca do
segredo do runtime.

### Critérios de aceitação

- A mesma operação e chave de idempotência devem produzir ou recuperar a mesma capability válida
  durante a vida útil da reserva.
- Reiniciar o runtime não deve invalidar o replay de `task_claim` ou `review_claim`.
- O texto da capability não deve ser persistido em claro.
- Separar o segredo efêmero da API do mecanismo estável necessário para derivar capabilities.
- Testar replay antes e depois de uma reinicialização e validar a capability retornada com
  `run_start` ou `review_start`.

## AB-BUG-004 — Aprovação humana de plano ausente no dashboard

### Comportamento observado

O Codex cria e valida uma revisão em estado `DRAFT`, apresenta o plano e aguarda a aprovação
humana. Entretanto, a tela **Planos** não oferece campo de confirmação nem botão de aprovação.
Sem a aprovação, o plano não muda para `ACTIVE`, as tasks não são materializadas e nada aparece
como elegível para execução.

Uma resposta afirmativa no chat não substitui a autorização autenticada exigida pelo domínio.
O Codex também não deve fabricar uma capability humana.

### Causa confirmada

O backend já possui:

- `POST /api/v1/authorizations`;
- `POST /api/v1/plans/{plan_id}/revisions/{revision}/approve`;
- validação de role `human` no serviço de domínio;
- ferramentas MCP `plan_approve` e `plan_revision_approve`.

O frontend também possui o helper genérico `requestHumanAuthorization`, utilizado no fluxo de
aplicação de configuração.

Porém, `web/src/PlansView.tsx` somente:

- lista revisões;
- consulta o conteúdo;
- valida o plano;
- exibe impacto e tasks lógicas.

Não existe:

- confirmação explícita do humano;
- função de API frontend para aprovar o plano;
- chamada a `requestHumanAuthorization("plan_approve", ...)`;
- envio de `expected_version` e chave de idempotência ao endpoint de aprovação;
- refresh do plano, quadro e scheduler após a aprovação;
- teste de UI cobrindo o fluxo.

Portanto, o backend está implementado, mas o fluxo vertical está incompleto.

### Escopo da autorização

A autorização atual usa somente `resource_id=plan_id`. A futura ação deve ser vinculada à revisão
exata que o humano inspecionou, incluindo ao menos `plan_id`, `revision` e a versão esperada. Também
deve ser decidido se a capability humana será consumida em uso único; atualmente ela pode ser
reutilizada dentro do TTL para a mesma operação e `resource_id`.

### Experiência esperada

1. O humano seleciona uma revisão `DRAFT`.
2. O dashboard mostra objetivo, target branch, tasks, dependências, paths, riscos, validação e
   impacto.
3. O botão permanece desabilitado se a revisão for inválida, não for `DRAFT`, estiver sem versão
   ou ainda estiver carregando.
4. O humano marca uma confirmação equivalente a “Revisei esta revisão e autorizo sua aprovação”.
5. O dashboard solicita uma capability curta e restrita à revisão.
6. O dashboard chama o endpoint de aprovação com `expected_version` e uma chave de idempotência
   estável durante retries.
7. O servidor registra o ator humano, ativa a revisão e materializa as tasks.
8. A tela atualiza Planos e Quadro; o Codex passa a encontrar as tasks pelo scheduler.

### Critérios de aceitação

- Adicionar confirmação explícita e ação **Autorizar e aprovar** à tela de Planos.
- Reutilizar o padrão seguro já empregado na aplicação de drafts de configuração.
- Exibir claramente o ID, revisão e versão que serão aprovados.
- Vincular a autorização à revisão exata e impedir uso para outra revisão.
- Preservar a mesma chave de idempotência quando o usuário repetir após erro transitório.
- Exibir sucesso, conflito de versão, expiração de autorização e falha de validação.
- Atualizar as projeções sem exigir reload manual.
- Adicionar teste de componente para estados habilitado/desabilitado e confirmação.
- Adicionar teste E2E/API: criar draft, validar, autorizar como humano, aprovar, verificar `ACTIVE`,
  tasks materializadas e `schedule_next` elegível.
- Confirmar que aprovação sem capability humana continua proibida.

## AB-BUG-005 — Bootstrap incompleto de projetos

### Comportamento observado

`agentboard init --project .` cria somente `agentboard.yaml`, mas o fluxo documentado e o skill
esperam orientação de projeto e perfis de agentes. Em projetos novos, o Codex encontra a
configuração, mas não encontra o restante do contexto necessário.

Além disso, o skill exige nomes específicos como `DEVELOPMENT_GUIDE.md` e
`docs/ARCHITECTURE.md`, embora esses documentos sejam próprios do repositório do AgentBoard e não
sejam obrigatórios para todo projeto consumidor.

### Critérios de aceitação

- `agentboard init --project .` deve criar, quando ausentes:
  - `agentboard.yaml`;
  - `AGENTS.md` mínimo;
  - `.codex/agents/orchestrator.toml`;
  - `.codex/agents/worker.toml`;
  - `.codex/agents/reviewer.toml`;
  - entrada `.agentboard/` em `.gitignore`.
- Nunca sobrescrever arquivos existentes sem uma opção explícita.
- Informar separadamente arquivos criados, reutilizados e ignorados.
- Validar o YAML e os perfis gerados.
- O skill deve ler `AGENTS.md`, `agentboard.yaml`, o perfil relevante e os documentos indicados
  pelo próprio `AGENTS.md`.
- `DEVELOPMENT_GUIDE.md` e `docs/ARCHITECTURE.md` devem ser opcionais.

## Robustez relacionada

Estes itens podem ser tratados na mesma rodada, mas não substituem as causas-raiz:

- Definir retenção limitada para `runtime.lock.stale.*`.
- Melhorar `runtime.log` com timestamp, PID, porta, identidade e motivo de takeover, sem registrar
  tokens.
- Detectar e relatar runtimes órfãos sem encerrar processos de forma destrutiva ou ambígua.
- Decidir se uma sessão/SSE ativa do dashboard deve impedir idle shutdown quando não há MCP
  conectado.
- Testar startup lento e a janela entre reservar uma porta livre e o `bind` efetivo.

## Ordem sugerida para implementação

1. AB-BUG-001, restaurando a autoridade de runtime único.
2. AB-BUG-003, garantindo capabilities e retries estáveis entre reinícios.
3. AB-BUG-002, isolando dashboards e cobrindo múltiplas portas.
4. AB-BUG-004, completando o fluxo humano de aprovação de planos.
5. AB-BUG-005, corrigindo o onboarding e as instruções do skill.
6. Robustez, limpeza, observabilidade e testes E2E.

## Validação da rodada

Para alterações Python:

```powershell
uv run pytest
uv run ruff check .
```

Para alterações no dashboard:

```powershell
Set-Location web
npm run lint
npm run build
```

Além dos gates gerais, a rodada deve apresentar evidência explícita para:

- múltiplos Codex compartilhando um runtime no Windows;
- dois dashboards simultaneamente autenticados;
- retry de claim após reinício;
- aprovação humana de uma revisão pelo dashboard;
- tasks disponibilizadas ao scheduler somente depois da aprovação;
- bootstrap completo e não destrutivo de um projeto novo.
