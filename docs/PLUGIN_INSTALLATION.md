# Instalação local do plugin AgentBoard

Este guia instala o AgentBoard para uma primeira rodada local no Codex. Ele não publica o plugin,
não acessa Git remoto e não cria pull requests.

O plugin possui duas partes:

1. o comando Python `agentboard`, usado pelo servidor MCP;
2. o bundle do Codex, com manifesto, skill, configuração MCP e hooks.

## Pré-requisitos

- Python 3.11 ou superior;
- Codex CLI com o comando `codex plugin`;
- Git local;
- Node.js LTS e npm somente quando for necessário reconstruir o dashboard.

Os comandos abaixo usam PowerShell no Windows. Em Linux ou macOS, use `python3`, caminhos POSIX e o
diretório de scripts de usuário correspondente.

Plugins podem ser usados no Codex CLI e no Codex do aplicativo desktop. Após instalar ou atualizar
um plugin, inicie uma nova conversa ou sessão para carregar suas skills, ferramentas MCP e hooks.

## 1. Instalar o comando `agentboard`

No PowerShell, a partir da raiz deste repositório:

```powershell
python -m pip install --user --upgrade .

$UserScripts = python -c "import sysconfig; print(sysconfig.get_path('scripts', scheme='nt_user'))"
$env:Path = "$UserScripts;$env:Path"

agentboard --help
```

O ajuste acima vale para o terminal atual. Para usar o Codex no aplicativo desktop, adicione
permanentemente o valor exibido em `$UserScripts` ao `PATH` do usuário e reinicie o aplicativo.

Para um teste restrito ao terminal, também é possível usar o ambiente virtual do projeto:

```powershell
.\.venv\Scripts\Activate.ps1
agentboard --help
codex
```

Nesse caso, inicie o Codex no mesmo terminal ativado. Um aplicativo Codex já aberto não herdará
esse ambiente virtual.

## 2. Gerar o marketplace local

O empacotador copia somente os componentes necessários ao plugin:

```powershell
python scripts\package_local_plugin.py
```

O resultado fica em:

```text
dist/agentboard-marketplace/
├── .agents/plugins/marketplace.json
└── plugins/agent-board/
    ├── .codex-plugin/plugin.json
    ├── .mcp.json
    ├── hooks/
    ├── scripts/
    └── skills/
```

Para reconstruir um bundle que já foi gerado:

```powershell
python scripts\package_local_plugin.py --force
```

O `--force` aceita substituir somente um diretório reconhecido como marketplace
`agentboard-local`; outros diretórios são preservados.

## 3. Registrar e instalar no Codex

Registre o marketplace gerado e instale o plugin:

```powershell
codex plugin marketplace add .\dist\agentboard-marketplace
codex plugin add agent-board@agentboard-local
codex plugin list
```

No Codex CLI, `/plugins` abre o navegador de plugins. No aplicativo desktop, abra **Plugins**,
localize **AgentBoard** na fonte **AgentBoard local** e confirme que ele está instalado e habilitado.

Revise [hooks/hooks.json](../hooks/hooks.json) antes de confiar nos hooks. O Codex não confia
automaticamente em hooks distribuídos por plugins. Quando solicitado:

1. confira os hooks `SessionStart` e `PreToolUse`;
2. autorize-os somente se o conteúdo corresponder ao repositório revisado;
3. inicie uma nova conversa ou sessão.

O hook ativa a proteção local do AgentBoard e bloqueia tentativas comuns de Git remoto ou
destrutivo. Ele é uma defesa adicional; o serviço de domínio e o adapter Git continuam sendo as
autoridades.

## 4. Verificar a instalação

Abra um projeto que contenha `agentboard.yaml` e execute:

```powershell
agentboard validate-config
agentboard status
```

Em uma nova conversa do Codex, use:

```text
Use o AgentBoard neste projeto. Abra o projeto, valide a configuração e mostre
as próximas tasks elegíveis sem iniciar nenhuma execução.
```

Depois, abra o dashboard:

```powershell
agentboard dashboard
```

Use somente a URL de bootstrap criada para essa execução; ela contém uma credencial local de uso
único e não deve ser copiada para logs ou tickets.

## Atualizar durante o desenvolvimento

Se o dashboard mudou, reconstrua seus assets antes de reinstalar o pacote Python:

```powershell
Set-Location web
npm ci
npm run build
Set-Location ..

python -m pip install --user --upgrade .
python scripts\package_local_plugin.py --force
```

Para invalidar o cache do plugin, use o helper oficial de desenvolvimento sobre o bundle gerado:

```powershell
$PluginCreator = Join-Path $env:USERPROFILE ".codex\skills\.system\plugin-creator"
python "$PluginCreator\scripts\update_plugin_cachebuster.py" `
  .\dist\agentboard-marketplace\plugins\agent-board

codex plugin add agent-board@agentboard-local
```

Reinicie o Codex e teste em uma nova conversa. O cachebuster altera somente o manifesto dentro de
`dist/`; o manifesto-fonte do repositório permanece intacto.

## Remover

```powershell
codex plugin remove agent-board@agentboard-local
codex plugin marketplace remove agentboard-local
python -m pip uninstall agent-board
```

Remover o plugin não apaga bancos ou worktrees existentes em `.agentboard/`. Preserve ou descarte
esses dados separadamente, depois de confirmar o projeto correto.

## Problemas comuns

- **`agentboard` não encontrado:** confirme o diretório de scripts do Python no `PATH` do processo
  que inicia o Codex.
- **MCP não aparece:** confirme `codex plugin list`, verifique se o plugin está habilitado e abra uma
  nova sessão.
- **Claim Git recusado por proteção ausente:** revise e autorize os hooks, confirme que `python`
  está no `PATH` e reinicie a sessão.
- **Dashboard sem alterações recentes:** execute `npm run build` em `web/` e reinstale o pacote
  Python.
- **Marketplace já registrado:** não o adicione novamente; apenas gere um novo bundle, aplique o
  cachebuster e execute `codex plugin add` outra vez.

## Referências oficiais

- [Empacotamento e marketplaces locais de plugins](https://developers.openai.com/plugins/build/plugins)
- [Instalação e uso de plugins](https://learn.chatgpt.com/docs/plugins)
