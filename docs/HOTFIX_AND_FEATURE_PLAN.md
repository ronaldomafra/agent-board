# Plano de hotfix e nova feature

**Status:** proposta para revisão e aprovação  
**Escopo:** estabilizar o AgentBoard atual e evoluir para um daemon único, multi-projeto e
multi-sessão.

## Objetivos

1. Corrigir os defeitos que impedem o uso seguro com várias instâncias do Codex.
2. Preservar configuração, banco operacional e políticas dentro de cada projeto.
3. Substituir servidores por projeto por um daemon único para o usuário local.
4. Exibir projetos, planos, execuções e sessões Codex em um dashboard consolidado.
5. Permitir desligamento seguro do daemon pelo dashboard.

## Invariantes propostos

- Deve existir no máximo um daemon AgentBoard por usuário do sistema.
- Cada projeto carregado possui exatamente um contexto identificado por `project_key`.
- Cada Codex mantém uma ponte MCP leve e registra uma sessão separada no daemon.
- Bancos, configurações, worktrees e evidências continuam isolados por projeto.
- Somente o serviço de domínio pode alterar tarefas, leases, WIP, runs e reviews.
- Toda mutação exige ator, chave de idempotência e versão esperada.
- O daemon permanece restrito a `127.0.0.1`.
- Nenhum token, banco ou metadata operacional pode ser versionado.

## Arquitetura alvo

```text
Codex A ── ponte MCP ──┐
Codex B ── ponte MCP ──┼── AgentBoard daemon ── Dashboard consolidado
Codex C ── ponte MCP ──┘          │
                                  ├── Contexto Projeto A
                                  │     ├── agentboard.yaml
                                  │     └── .agentboard/agentboard.db
                                  ├── Contexto Projeto B
                                  │     ├── agentboard.yaml
                                  │     └── .agentboard/agentboard.db
                                  └── Registro de sessões Codex
```

O daemon não deve centralizar os bancos dos projetos. Ele centraliza apenas descoberta, processo
HTTP, autenticação do dashboard, sessões conectadas e gerenciamento dos contextos carregados.

# Parte 1 — Hotfixes

## HF-01 — Corrigir singleton no Windows

**Gravidade:** crítica

### Problema

A detecção de PID vivo usa `os.kill(pid, 0)`, que pode retornar `WinError 87` para processos
externos vivos no Windows. O runtime considera o proprietário morto, toma seu lock e inicia outro
servidor.

### Entrega

- Implementar detecção de processo compatível com Windows.
- Impedir takeover de um proprietário vivo mesmo quando o health check falhar temporariamente.
- Fazer o runtime detectar perda de propriedade e encerrar de maneira controlada.
- Limitar ou remover arquivos `runtime.lock.stale.*` antigos.
- Melhorar logs de startup e takeover sem registrar tokens.

### Testes

- Processo externo vivo no Windows é reconhecido.
- Processo morto permite recuperação do lock.
- PID reutilizado é diferenciado do proprietário original.
- Várias chamadas concorrentes iniciam somente um processo e uma porta.
- Startup lento não gera runtimes órfãos.

## HF-02 — Isolar cookies do dashboard

**Gravidade:** alta

### Problema

`agentboard_session` e `agentboard_csrf` usam nomes e caminho iguais em todas as portas de
`127.0.0.1`. O navegador substitui os cookies ao abrir outro dashboard.

### Entrega

- Definir um namespace coerente para sessão e CSRF.
- Manter backend e frontend usando a mesma identificação.
- Definir expiração e limpeza de cookies antigos.
- Preservar a possibilidade de abrir mais de um dashboard durante a migração para o daemon único.

### Testes

- Dois dashboards em portas diferentes permanecem autenticados.
- CSRF de um runtime não é aceito por outro.
- Reabrir um dashboard não invalida a outra sessão.

## HF-03 — Tornar capabilities estáveis entre reinícios

**Gravidade:** alta

### Problema

Capabilities são derivadas do token efêmero do runtime. Depois de um reinício, um retry com a
mesma chave de idempotência produz outra capability e pode gerar conflito ou devolver uma
credencial incompatível com o hash persistido.

### Entrega

- Separar o segredo da API do mecanismo estável de derivação de capabilities.
- Continuar sem persistir capabilities em texto claro.
- Recuperar a mesma capability válida para um replay idempotente.
- Preservar capabilities de worker e reviewer durante a validade da reserva.

### Testes

- Replay de `task_claim` antes e depois do reinício.
- Replay de `review_claim` antes e depois do reinício.
- Capability retornada funciona em `run_start` ou `review_start`.
- Payload diferente com a mesma chave continua gerando conflito.

## HF-04 — Adicionar aprovação humana de planos

**Gravidade:** alta

### Problema

O backend possui autorização e endpoint de aprovação, mas a tela de Planos não possui confirmação
nem ação para aprovar uma revisão. O plano permanece `DRAFT` e suas tasks não ficam disponíveis.

### Entrega

- Exibir ID, revisão, versão, objetivo, tasks, dependências, paths, riscos e impacto.
- Adicionar confirmação explícita do humano.
- Adicionar ação **Autorizar e aprovar**.
- Solicitar capability humana curta e vinculada à revisão exata.
- Enviar `expected_version` e chave de idempotência estável.
- Atualizar Planos, Quadro e scheduler após sucesso.
- Exibir conflito, validação inválida e autorização expirada.

### Testes

- Botão desabilitado para revisão inválida ou que não esteja em `DRAFT`.
- Aprovação sem capability humana continua proibida.
- Capability de uma revisão não aprova outra.
- Aprovação muda o plano para `ACTIVE` e materializa as tasks.
- Tasks aprovadas aparecem como elegíveis no scheduler.

## HF-05 — Completar `agentboard init`

**Gravidade:** média

### Problema

O comando cria somente `agentboard.yaml`, mas o fluxo espera regras do projeto e perfis Codex.

### Entrega

Criar, somente quando ausentes:

- `agentboard.yaml`;
- `AGENTS.md`;
- `.codex/agents/orchestrator.toml`;
- `.codex/agents/worker.toml`;
- `.codex/agents/reviewer.toml`;
- entrada `.agentboard/` em `.gitignore`.

O comando deve informar arquivos criados, reutilizados e ignorados e nunca sobrescrever conteúdo
sem autorização explícita.

### Ajuste do skill

- Tornar `DEVELOPMENT_GUIDE.md` e `docs/ARCHITECTURE.md` opcionais.
- Ler os documentos indicados pelo `AGENTS.md`.
- Continuar exigindo `agentboard.yaml` e o perfil relevante para operações AgentBoard.

### Testes

- Bootstrap em diretório vazio.
- Bootstrap em projeto com arquivos preexistentes.
- Segunda execução idempotente.
- Validação do YAML e dos três perfis gerados.

# Parte 2 — Nova feature: daemon global multi-projeto

## NF-01 — Descoberta e singleton global

### Objetivo

Substituir metadata e lock de processo por projeto por metadata e lock globais para o usuário do
sistema.

### Entrega

- Definir diretório operacional global, privado e não versionado.
- Implementar lock global compatível com Windows, Linux e macOS.
- Fazer a primeira ponte MCP iniciar o daemon quando ausente.
- Fazer as pontes seguintes reutilizarem PID, porta e geração existentes.
- Validar saúde, versão de protocolo e identidade do daemon.
- Manter metadata dos projetos dentro de seus respectivos diretórios.

### Critérios de aceitação

- Dez pontes MCP simultâneas resultam em um daemon.
- Apenas uma porta permanece em `LISTENING`.
- Encerrar uma ponte não encerra o daemon enquanto outras estiverem conectadas.
- Crash permite recuperação segura do lock.

## NF-02 — Registro de contextos de projeto

### Objetivo

Permitir que um processo carregue vários projetos sem misturar configuração ou estado.

### Entrega

- Criar um `ProjectContext` por `project_key`.
- Canonicalizar raiz real e Git common directory.
- Carregar `agentboard.yaml`, `BoardService`, SQLite, SSE e sweeper por contexto.
- Roteamento de API e MCP sempre escopado pelo projeto.
- Lazy load do contexto no primeiro attach.
- Unload automático quando o projeto estiver ocioso e sem trabalho ativo.
- Isolar falhas para que um projeto não derrube os demais.

### Critérios de aceitação

- Dois projetos usam o mesmo daemon e bancos diferentes.
- Configuração e WIP de um projeto não afetam outro.
- Capability de um projeto não é aceita em outro.
- Descarregar um contexto fecha recursos sem encerrar o daemon.

## NF-03 — Sessões Codex

### Objetivo

Representar cada ponte MCP conectada como uma sessão operacional independente.

### Dados mínimos

- `session_id`;
- `client_id`;
- `project_key`;
- raiz do projeto;
- horário de conexão;
- último heartbeat;
- versão do plugin e protocolo;
- status `ATTACHED`, `STALE` ou `DETACHED`;
- identificador nativo do Codex, quando disponível.

Sessão Codex não substitui agente, assignment, lease ou run. A relação com uma execução deve ser
feita somente quando houver identificador confiável.

### Entrega

- Estender attach, heartbeat e detach.
- Expor listagem de sessões para o dashboard.
- Remover sessões expiradas.
- Manter sessões ativas em memória; persistir histórico somente se houver requisito de auditoria.

### Critérios de aceitação

- Cada Codex aparece separadamente.
- Sessão sem heartbeat passa para `STALE` e é removida após o TTL.
- Fechar um Codex remove somente sua sessão.
- Várias sessões podem operar no mesmo projeto e em projetos diferentes.

## NF-04 — Dashboard consolidado

### Objetivo

Oferecer uma única página para acompanhar todos os projetos e sessões.

### Entrega

- Visão geral do daemon: versão, PID, porta, geração, uptime e memória.
- Lista de projetos carregados.
- Lista de sessões Codex conectadas.
- Planos ativos e contagem de tasks por estado.
- Runs, reviews, leases e alertas ativos.
- Seletor de projeto para abrir Planos, Quadro, Agents e Execuções.
- Ação para descarregar um projeto ocioso.
- Atualizações por SSE com `project_key` em cada evento.

### Critérios de aceitação

- Alteração em qualquer projeto aparece sem refresh.
- Selecionar um projeto nunca mistura dados de outro.
- Dashboard continua utilizável com apenas um projeto.
- Estados vazio, carregando, erro e sessão stale são acessíveis.

## NF-05 — Desligamento seguro pelo dashboard

### Objetivo

Permitir liberar memória sem interromper silenciosamente trabalho ativo.

### Estados do daemon

- `RUNNING`;
- `DRAINING`;
- `STOPPING`.

### Entrega

- Endpoint somente leitura para calcular impacto do desligamento.
- Listar sessões, reservas, runs, tasks, reviews, leases e operações externas afetadas.
- Adicionar autorização humana `daemon_shutdown`.
- Exigir geração esperada do daemon e chave de idempotência.
- Adicionar botão **Encerrar AgentBoard**.
- Quando não houver trabalho ativo, permitir **Encerrar agora**.
- Quando houver trabalho ativo, oferecer **Encerrar quando ocioso**.
- Em `DRAINING`, bloquear novos claims e runs, mas permitir heartbeat, resultado, revisão e
  finalização.
- Encerrar após não haver trabalho operacional nem mutações em andamento.
- Remover metadata e lock globais somente pelo proprietário.

Um plano `ACTIVE` apenas com tasks em `BACKLOG` não bloqueia o desligamento, pois seu estado está
persistido. O bloqueio considera trabalho realmente em andamento.

### Critérios de aceitação

- Desligamento ocioso encerra o processo e limpa metadata.
- Trabalho ativo produz aviso detalhado e impede parada imediata.
- Modo draining permite concluir runs existentes.
- Nenhuma task é marcada como concluída ou cancelada apenas por desligar o daemon.
- O próximo uso do plugin inicia exatamente um novo daemon.

## NF-06 — Compatibilidade e atualização

### Objetivo

Evitar que um plugin novo se conecte silenciosamente a um daemon incompatível.

### Entrega

- Handshake com versão do daemon, plugin, API, schema e protocolo MCP.
- Erro acionável para versões incompatíveis.
- Reinício seguro quando o daemon estiver ocioso.
- Aviso e modo draining quando houver trabalho ativo.
- Migração dos antigos `runtime.json` e `runtime.lock` por projeto.
- Detecção de runtimes órfãos sem encerramento destrutivo automático.

### Critérios de aceitação

- Cliente compatível conecta normalmente.
- Cliente incompatível não executa mutações.
- Atualização não perde estado operacional.
- Runtime antigo ativo é reportado com instruções seguras de recuperação.

# Sequência recomendada

1. HF-01 — singleton confiável no Windows.
2. HF-03 — capabilities estáveis.
3. NF-01 — singleton global e handshake mínimo.
4. NF-02 — contextos multi-projeto.
5. NF-03 — sessões Codex.
6. NF-04 — dashboard consolidado.
7. NF-05 — desligamento seguro e draining.
8. NF-06 — compatibilidade e migração.
9. HF-02 — compatibilidade de cookies durante a transição.
10. HF-04 — aprovação humana de planos.
11. HF-05 — bootstrap completo.
12. Testes E2E, documentação, empacotamento e atualização do plugin.

# Estratégia de tarefas e leases

As alterações centrais devem ser sequenciais porque compartilham arquivos sensíveis:

- `src/agentboard/runtime.py`;
- `src/agentboard/service.py`;
- `src/agentboard/web.py`;
- migrations.

Trabalhos de UI podem ocorrer em paralelo somente quando seus arquivos não se sobrepuserem.
Nenhum worker deve modificar `domain.py`, `service.py` ou migrations simultaneamente com outro
worker.

# Gates de validação

## Python

```powershell
uv run pytest
uv run ruff check .
```

## Dashboard

```powershell
Set-Location web
npm run lint
npm run build
```

## Cenários E2E obrigatórios

- várias instâncias do Codex, um daemon e várias sessões;
- dois projetos simultâneos com bancos isolados;
- aprovação humana de plano e materialização de tasks;
- retry de claim após reinício;
- dashboard consolidado recebendo eventos dos dois projetos;
- unload de contexto ocioso;
- tentativa de desligamento com run ativo;
- draining e desligamento após conclusão;
- reinicialização automática no próximo uso do plugin;
- atualização com incompatibilidade de protocolo;
- bootstrap completo de um projeto novo.

# Riscos

- O daemon global se torna um ponto único de falha para todos os projetos ativos.
- Autenticação e capabilities precisam ser rigorosamente escopadas por projeto.
- Um projeto não pode controlar a política global de desligamento dos demais.
- A configuração `runtime.idle_shutdown_seconds` precisa ser migrada ou reinterpretada.
- Contextos ociosos precisam ser descarregados para o daemon não acumular memória.
- Mudanças no protocolo exigem compatibilidade coordenada entre CLI, plugin, MCP e dashboard.

# Definição de concluído

A rodada termina somente quando:

- os hotfixes possuem testes de regressão;
- várias instâncias do Codex compartilham um daemon;
- projetos permanecem isolados;
- o dashboard lista sessões e trabalho ativo;
- a aprovação humana de planos funciona de ponta a ponta;
- o desligamento seguro funciona com e sem trabalho ativo;
- os gates Python e frontend passam;
- o plugin é reconstruído, validado e reinstalado;
- documentação de instalação, operação, recuperação e atualização está consistente.
