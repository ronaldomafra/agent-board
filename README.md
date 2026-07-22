# AgentBoard

Control plane local-first para agentes de desenvolvimento com IA. O AgentBoard combina um plugin Codex, um servidor MCP, um dashboard local e orquestração de tasks persistida em SQLite.

## Status do projeto

O repositório contém a fundação arquitetural e os contratos do primeiro fluxo vertical. Ele ainda não é um pacote publicado nem um plugin pronto para produção.

## Objetivos

- Coordenar tasks de desenvolvimento entre agentes Codex.
- Aplicar dependências, limites de WIP, leases de tasks e transições de estado válidas.
- Manter a configuração no Git e o histórico operacional em SQLite local.
- Expor as mesmas regras de domínio por tools MCP e por um dashboard web local.
- Manter os serviços em `localhost` e ativos somente enquanto o Codex estiver em execução no MVP.

## Visão da solução

```mermaid
flowchart LR
    subgraph Codex["Codex"]
        Plugin["Plugin e skill"]
        Agents["Orquestrador e agentes"]
    end

    subgraph AgentBoard["AgentBoard local"]
        MCP["MCP stdio"]
        Core["Serviço de domínio"]
        DB[("SQLite")]
        Web["Dashboard HTTP e SSE"]
    end

    Browser["Navegador"]

    Plugin --> Agents
    Agents -->|"tools MCP"| MCP
    MCP --> Core
    Core <--> DB
    Core --> Web
    Web -->|"localhost"| Browser

    classDef codex fill:#2a1b16,stroke:#ff7a1a,color:#fff7f1,stroke-width:2px
    classDef mcp fill:#2d1b33,stroke:#c38cff,color:#fff7f1,stroke-width:2px
    classDef core fill:#193126,stroke:#54d6a0,color:#fff7f1,stroke-width:2px
    classDef data fill:#172938,stroke:#77a7ff,color:#fff7f1,stroke-width:2px
    classDef dashboard fill:#3a2518,stroke:#ffab62,color:#fff7f1,stroke-width:2px
    class Plugin,Agents codex
    class MCP mcp
    class Core core
    class DB data
    class Web,Browser dashboard
```

O Codex usa o MCP para executar operações do AgentBoard. O dashboard é uma interface web local e consulta o mesmo serviço de domínio; ele não acessa o banco diretamente.

## Fluxo de uma task

```mermaid
flowchart LR
    Backlog["BACKLOG"] --> Ready["READY"]
    Ready --> Assigned["ASSIGNED"]
    Assigned --> Progress["IN PROGRESS"]
    Progress --> Verify["VERIFYING"]
    Verify --> Done["DONE"]

    Ready --> Blocked["BLOCKED"]
    Assigned --> Blocked
    Progress --> Blocked
    Verify --> Blocked
    Progress --> Rework["REWORK"]
    Verify --> Rework
    Rework --> Progress
    Blocked --> Ready
    Blocked --> Progress
    Backlog --> Canceled["CANCELED"]
    Ready --> Canceled
    Blocked --> Canceled
    Rework --> Canceled

    classDef planned fill:#2a2117,stroke:#ffab62,color:#fff7f1,stroke-width:2px
    classDef active fill:#392d11,stroke:#ffc34d,color:#fff7f1,stroke-width:2px
    classDef complete fill:#173326,stroke:#54d6a0,color:#fff7f1,stroke-width:2px
    classDef exception fill:#3b1920,stroke:#ff6878,color:#fff7f1,stroke-width:2px
    class Backlog,Ready,Assigned planned
    class Progress,Verify active
    class Done complete
    class Blocked,Rework,Canceled exception
```

As mudanças de estado são validadas pelo serviço de domínio. O AgentBoard verifica dependências, limite de WIP, lease ativo, versão esperada e evidências antes de aceitar uma transição.

## Ciclo operacional

```mermaid
sequenceDiagram
    participant O as Orquestrador
    participant M as MCP AgentBoard
    participant W as Worker
    participant R as Reviewer
    participant D as Dashboard

    O->>M: task_claim
    M-->>D: Evento de lease e status
    O->>W: Task, escopo e arquivos sob lease
    W->>M: task_heartbeat
    M-->>D: Progresso em tempo real
    W->>M: task_report_result
    O->>R: Solicita revisão
    R->>M: task_transition para DONE ou REWORK
    M-->>D: Evidência e estado final
```

## Estrutura do repositório

| Caminho | Finalidade |
| --- | --- |
| `src/agentboard/` | Domínio Python, persistência, serviços e adapters MCP/web |
| `web/` | Código-fonte do dashboard React/Vite |
| `skills/` | Fluxo Codex para orquestração de tasks |
| `.codex/agents/` | Perfis de agentes específicos do projeto |
| `config/` | Exemplos versionados de configuração de orquestração |
| `docs/` | Arquitetura e documentação de desenvolvimento |
| `tests/` | Testes do domínio e dos adapters |

## Desenvolvimento

```bash
uv sync --all-groups
uv run pytest
uv run ruff check .
uv run agentboard --help
```

Leia o [guia de desenvolvimento](DEVELOPMENT_GUIDE.md) antes de implementar uma funcionalidade. As regras específicas para o Codex estão em [AGENTS.md](AGENTS.md).

## Licença

Apache-2.0
