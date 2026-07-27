# AgentBoard

AgentBoard é um control plane local-first para planejar, agendar, executar e revisar trabalho
feito por agentes Codex. A configuração declarativa permanece no Git; tasks, leases, runs,
reviews, evidências e eventos ficam no SQLite local.

O fluxo principal é:

`requisitos → plano → aprovação → scheduler → reserva → spawn Codex → run → review → Git local → DONE`

## Limites do produto

- O serviço de domínio é a única autoridade para fases, dependências, WIP e leases.
- MCP, HTTP e dashboard são adapters finos sobre o mesmo serviço.
- O runtime e o dashboard escutam somente em `127.0.0.1`.
- O Codex cria os subagentes nativamente; o AgentBoard reserva e registra o trabalho.
- Git é estritamente local. Não há `push`, `pull`, `fetch`, `clone`, `ls-remote`, GitHub, PR,
  deploy, `reset` destrutivo ou `stash` automático.
- Hooks Codex são defesa adicional, não uma fronteira de segurança. O adapter Git, as capabilities
  e o serviço de domínio continuam sendo a garantia principal.

## Arquitetura

```text
Codex / subagentes --stdio MCP--> ponte MCP --loopback autenticado--+
Dashboard ---------------------HTTP/SSE-----------------------------+--> runtime
                                                                     +--> serviço de domínio
                                                                     +--> SQLite
Configuração versionada --------------------------------------------+--> validação/política
```

Existe no máximo um runtime por projeto. A identidade combina o caminho real do projeto e o Git
common directory. Banco, lock, PID, nonce, porta, backups e worktrees ficam em `.agentboard/` e
nunca devem ser versionados.

## Pré-requisitos

- Python 3.11 ou superior
- `uv` para desenvolvimento
- Node.js LTS e npm para construir o dashboard
- Git local quando o perfil de evidência exigir checkpoints ou integração

## Instalação de desenvolvimento

```bash
uv sync --all-groups
cd web
npm ci
npm run build
cd ..
uv run agentboard --help
```

Para usar o plugin no Codex, instale o CLI no ambiente que inicia o Codex e gere o marketplace
local compacto. O arquivo `.mcp.json` chama diretamente `agentboard mcp`; ele não usa `uvx` nem
baixa pacotes durante a sessão.

O procedimento completo, incluindo o marketplace local, confiança dos hooks e atualização do
bundle, está em [docs/PLUGIN_INSTALLATION.md](docs/PLUGIN_INSTALLATION.md).

## Primeira execução

1. Copie `config/orchestration.example.yaml` para `agentboard.yaml` e ajuste a política.
2. Valide a configuração antes de aplicá-la.
3. Inicie `agentboard runtime` ou deixe `agentboard mcp` localizar/iniciar o runtime local.
4. No Codex, invoque a skill `agentboard-orchestrate`.
5. Abra o dashboard pelo bootstrap retornado por `dashboard_open`; não compartilhe a URL.

O processo de configuração é sempre `rascunho → validação → diff → aplicação explícita`.
Configuração nova vale para claims novos; runs existentes continuam vinculados à revisão anterior.

Para a primeira rodada manual, use o projeto estático
[Loja Exemplo](examples/loja-exemplo/README.md), com páginas inicial, produtos e sobre, configuração
AgentBoard e um cenário pequeno de tasks.

## Comandos de desenvolvimento

```bash
uv run pytest
uv run ruff check .
uv run agentboard runtime
uv run agentboard mcp
```

```bash
cd web
npm test
npm run lint
npm run build
```

Para validar o bundle do plugin:

```bash
python "/path/to/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py" .
python scripts/codex_hook.py --self-test
```

Consulte [docs/OPERATIONS.md](docs/OPERATIONS.md) para bootstrap, recuperação, backup e segurança,
[DEVELOPMENT_GUIDE.md](DEVELOPMENT_GUIDE.md) para os marcos e
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) para os limites de dados.

## Licença

Apache-2.0
