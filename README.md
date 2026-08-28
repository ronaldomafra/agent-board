# AgentBoard — plugin local para o Codex

AgentBoard é um plugin para planejar, coordenar e revisar trabalho executado por agentes do Codex.
Ele funciona como um control plane local: organiza planos, tarefas, dependências, limites de
trabalho em andamento, reservas, execuções, revisões e evidências sem depender de um serviço em
nuvem.

O Codex continua responsável por criar e conversar com os agentes. O AgentBoard controla quem pode
trabalhar em cada tarefa, quais arquivos estão reservados e quais evidências são necessárias antes
de considerar o trabalho concluído.

## Propósito

Use o AgentBoard quando quiser:

- transformar requisitos em um plano explícito e aprovável;
- ordenar tarefas por prioridade e dependências;
- impedir que agentes alterem os mesmos arquivos simultaneamente;
- acompanhar leases, heartbeats, execuções e bloqueios;
- exigir testes, evidências e revisão antes de concluir uma tarefa;
- visualizar o trabalho em um dashboard local;
- manter um histórico operacional auditável em SQLite.

O fluxo principal é:

```text
requisitos → plano → aprovação → agendamento → reserva → execução → evidências → revisão → conclusão
```

## O que o plugin instala

| Componente | Função |
| --- | --- |
| Skill `agentboard-orchestrate` | Orienta o Codex a planejar, reservar, executar e revisar tarefas pelo AgentBoard. |
| Servidor MCP | Disponibiliza ferramentas como `project_open`, `board_snapshot`, `task_claim`, `run_start` e `review_decide`. |
| Hooks do Codex | Ativam a proteção da sessão e bloqueiam tentativas comuns de contornar a política Git local. |
| CLI `agentboard` | Inicia o MCP, o runtime, os hooks, o dashboard e os comandos de configuração. |
| Dashboard local | Exibe board, tarefas, agentes, execuções, evidências e eventos. |

No Windows, o `pip` cria `agentboard.exe`. No Linux e no macOS, ele cria o executável
`agentboard`. Ambos são launchers para o mesmo pacote Python.

O plugin utiliza estes comandos automaticamente:

```text
agentboard mcp
agentboard codex-hook --activate
agentboard codex-hook
```

## Como funciona

```text
Codex / agentes --stdio MCP--> ponte AgentBoard --loopback autenticado--+
Dashboard --------------------HTTP/SSE----------------------------------+--> runtime local
                                                                         +--> serviço de domínio
                                                                         +--> SQLite
agentboard.yaml ---------------------------------------------------------+--> política do projeto
```

- Existe no máximo um runtime ativo por projeto.
- O runtime e o dashboard escutam somente em `127.0.0.1`.
- O estado operacional fica em `.agentboard/` e não deve ser versionado.
- O runtime encerra automaticamente após o último cliente MCP se desconectar.
- MCP, API e dashboard são adapters; as regras de tarefa, WIP e lease pertencem ao serviço de
  domínio.

## Requisitos

- Python 3.11 ou superior;
- Codex CLI ou aplicativo Codex com suporte a plugins;
- permissão para registrar um marketplace local;
- Git apenas se o projeto habilitar checkpoints ou integrações Git locais;
- Node.js e npm somente para desenvolver ou reconstruir o dashboard.

Não é necessário manter uma `.venv` ativada para usar o plugin. O comando `agentboard` precisa estar
instalado em um diretório presente no `PATH` do processo que inicia o Codex.

## Instalação

Execute os comandos a partir da raiz deste repositório.

### 1. Instalar o CLI no Windows

No PowerShell:

```powershell
python -m pip install --user --upgrade .

$UserScripts = python -c "import sysconfig; print(sysconfig.get_path('scripts', scheme='nt_user'))"
$env:Path = "$UserScripts;$env:Path"

agentboard --help
agentboard codex-hook --self-test
```

Para o Codex Desktop encontrar `agentboard.exe`, adicione `$UserScripts` permanentemente ao `PATH`
do usuário:

```powershell
$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
$Entries = @($UserPath -split ";" | Where-Object { $_ })

if ($Entries -notcontains $UserScripts) {
    [Environment]::SetEnvironmentVariable(
        "Path",
        (($Entries + $UserScripts) -join ";"),
        "User"
    )
}
```

Feche todas as janelas do Codex e abra o aplicativo novamente depois de alterar o `PATH`.

### 2. Instalar o CLI no Linux ou macOS

```bash
python3 -m pip install --user --upgrade .

USER_SCRIPTS="$(python3 -c 'import sysconfig; print(sysconfig.get_path("scripts", scheme="posix_user"))')"
export PATH="$USER_SCRIPTS:$PATH"

command -v agentboard
agentboard --help
agentboard codex-hook --self-test
```

Adicione o diretório retornado em `USER_SCRIPTS` ao `PATH` permanente da sua sessão. Na maioria das
distribuições Linux, a configuração equivalente é:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Se a distribuição bloquear instalações com `pip --user`, use um instalador isolado disponível no
sistema, como `pipx install .`, e confirme que o diretório de executáveis do `pipx` está no `PATH`.

### 3. Gerar o marketplace local

Windows:

```powershell
python scripts\package_local_plugin.py
```

Linux ou macOS:

```bash
python3 scripts/package_local_plugin.py
```

O bundle será criado em:

```text
dist/agentboard-marketplace/
├── .agents/plugins/marketplace.json
└── plugins/agent-board/
    ├── .codex-plugin/plugin.json
    ├── .mcp.json
    ├── hooks/
    └── skills/
```

Se o bundle já existir, gere-o novamente com:

```bash
python scripts/package_local_plugin.py --force
```

No Linux ou macOS, substitua `python` por `python3` quando necessário.

### 4. Registrar e instalar o plugin

O marketplace `agentboard-local` fica dentro do repositório e precisa ser registrado uma vez:

```bash
codex plugin marketplace add ./dist/agentboard-marketplace
codex plugin add agent-board@agentboard-local
codex plugin list
```

No aplicativo Codex, abra **Plugins**, localize **AgentBoard** na fonte **AgentBoard local** e
confirme que o plugin está instalado e habilitado.

### 5. Autorizar os hooks

O Codex solicitará confiança para os hooks distribuídos pelo plugin. Antes de autorizar, confira
[hooks/hooks.json](hooks/hooks.json). Os comandos esperados são:

```text
SessionStart → agentboard codex-hook --activate
PreToolUse   → agentboard codex-hook
```

Depois de instalar, atualizar ou aprovar os hooks, abra uma nova conversa. Skills, ferramentas MCP
e hooks não são recarregados dinamicamente em uma conversa que já estava aberta.

## Verificar a instalação

Windows:

```powershell
Get-Command agentboard
agentboard codex-hook --self-test
codex plugin list
```

Linux ou macOS:

```bash
command -v agentboard
agentboard codex-hook --self-test
codex plugin list
```

O self-test deve retornar um resultado semelhante a:

```json
{"ok": true, "cases": 26}
```

## Configurar o primeiro projeto

Entre na pasta que será controlada pelo AgentBoard:

```bash
agentboard init --project .
agentboard validate-config --project .
```

Isso cria, sem substituir arquivos existentes, `agentboard.yaml`, um `AGENTS.md` mínimo, os perfis
`.codex/agents/agentboard_{orchestrator,worker,reviewer}.toml` e a entrada local `.agentboard/` no
`.gitignore`. O comando informa separadamente os itens criados, reutilizados e já ignorados.

Se o projeto não utilizar Git, altere a configuração gerada para:

```yaml
git:
  enabled: false
```

Em uma nova conversa do Codex, use:

```text
Use $agent-board:agentboard-orchestrate neste projeto.
Abra o projeto, valide a configuração e mostre as próximas tarefas elegíveis
sem iniciar nenhuma execução.
```

Para abrir o dashboard:

```bash
agentboard dashboard --project .
```

O comando gera uma URL local de uso único. Não copie URLs de bootstrap, tokens ou arquivos de
`.agentboard/` para logs, commits ou tickets.

### Métricas de execução

Ao encerrar uma run por `task_report_result`, `run_fail` ou `task_block`, o agente pode informar
os totais reais da plataforma que o executou:

```json
{
  "usage": {
    "input_tokens": 1200,
    "output_tokens": 340
  }
}
```

Os valores são opcionais, ficam associados à tentativa auditável e aparecem na tela
**Execuções**. A duração é calculada de `run_start` até a task alcançar `DONE`, incluindo revisão
e integração local quando aplicáveis.

Para uma primeira experiência pronta, consulte o projeto
[Loja Modelo](examples/loja-exemplo/README.md).

## Segurança e limites

- O AgentBoard não executa `push`, `pull`, `fetch`, `clone` ou `ls-remote`.
- Não cria pull requests, não faz deploy e não chama integrações remotas automaticamente.
- Não executa `reset` destrutivo nem `stash` automático.
- Hooks são defesa adicional; o serviço de domínio, as capabilities e o adapter Git local continuam
  sendo as autoridades.
- Toda alteração de estado exige ator autenticado, chave de idempotência e versão esperada.
- Uma tarefa em `DONE` precisa de evidências; uma tarefa em `IN_PROGRESS` precisa de lease válido.
- SQLite contém somente estado operacional. A política declarativa permanece em
  `agentboard.yaml` e nos perfis `.codex/agents/`.

## Comandos principais

```text
agentboard mcp                         Inicia a ponte MCP por stdio
agentboard runtime --project .         Inicia o runtime local
agentboard dashboard --project .       Abre o dashboard autenticado
agentboard status --project .          Consulta o runtime
agentboard init --project .            Cria o scaffold não destrutivo do AgentBoard
agentboard validate-config --project . Valida agentboard.yaml
agentboard codex-hook --self-test      Testa o hook do plugin
```

## Atualizar o plugin durante o desenvolvimento

No Windows, atualize o pacote Python e reconstrua o bundle:

```powershell
python -m pip install --user --upgrade .
python scripts/package_local_plugin.py --force
```

No Linux ou macOS:

```bash
python3 -m pip install --user --upgrade .
python3 scripts/package_local_plugin.py --force
```

Atualize o cachebuster do bundle gerado no Linux ou macOS:

```bash
python3 ~/.codex/skills/.system/plugin-creator/scripts/update_plugin_cachebuster.py \
  ./dist/agentboard-marketplace/plugins/agent-board
```

No PowerShell, use o caminho equivalente dentro de `$env:USERPROFILE`:

```powershell
$PluginCreator = Join-Path $env:USERPROFILE ".codex\skills\.system\plugin-creator"
python "$PluginCreator\scripts\update_plugin_cachebuster.py" `
  .\dist\agentboard-marketplace\plugins\agent-board
```

Reinstale e abra uma nova conversa:

```bash
codex plugin add agent-board@agentboard-local
```

O cachebuster altera somente o manifesto no bundle gerado em `dist/`; o manifesto-fonte continua
com a versão base.

## Solução de problemas

### `agentboard` não encontrado

O diretório de scripts do Python não está no `PATH` do processo que abriu o Codex.

- Windows: execute `Get-Command agentboard`.
- Linux/macOS: execute `command -v agentboard`.
- Windows: confirme o diretório com
  `python -c "import sysconfig; print(sysconfig.get_path('scripts', scheme='nt_user'))"`.
- Linux/macOS: confirme o diretório com
  `python3 -c 'import sysconfig; print(sysconfig.get_path("scripts", scheme="posix_user"))'`.
- Feche e reabra o Codex depois de corrigir o `PATH`.

### `SessionStart hook (failed)` ou `PreToolUse hook (failed)`

Execute:

```bash
agentboard codex-hook --self-test
```

Se o teste passar, confirme que o Codex foi reiniciado depois da instalação e autorize novamente os
hooks caso os comandos tenham mudado.

### O MCP ou a skill não aparece

```bash
codex plugin list
```

Confirme que `agent-board@agentboard-local` está instalado e habilitado. Em seguida, abra uma nova
conversa.

### Marketplace já registrado

Não execute `codex plugin marketplace add` novamente. Reconstrua o bundle, atualize o cachebuster e
execute apenas:

```bash
codex plugin add agent-board@agentboard-local
```

### Projeto sem Git

Use `git.enabled: false` no `agentboard.yaml`. Operações que exigem checkpoints ou integração Git
não estarão disponíveis, mas planejamento, tarefas, leases, evidências e revisão continuam
funcionando.

## Desenvolvimento

Backend e testes:

```bash
uv sync --all-groups
uv run pytest
uv run ruff check .
```

Dashboard:

```bash
cd web
npm ci
npm test
npm run lint
npm run build
```

Documentação adicional:

- [Instalação detalhada](docs/PLUGIN_INSTALLATION.md)
- [Operação e recuperação](docs/OPERATIONS.md)
- [Arquitetura](docs/ARCHITECTURE.md)
- [Guia de desenvolvimento](DEVELOPMENT_GUIDE.md)

## Licença

Apache-2.0
