# Operação local do AgentBoard

Este guia cobre o runtime de projeto, configuração, recuperação e os controles locais. Ele não
autoriza publicação, acesso a Git remoto, pull requests ou deploy.

## Runtime singleton

`agentboard runtime` é o único processo que possui o SQLite, o serviço de domínio, a API HTTP e o
stream SSE. `agentboard mcp` é uma ponte stdio: ela deriva a identidade do projeto, encontra o
runtime registrado ou inicia um novo processo e se conecta por loopback autenticado.

O período ocioso é armado no startup: se nenhum cliente MCP chegar a se anexar, ou depois que o
último cliente sair, o runtime encerra normalmente ao fim da tolerância configurada.

Os metadados ficam sob `.agentboard/`:

| Arquivo/diretório | Conteúdo |
| --- | --- |
| `agentboard.db` | estado operacional SQLite |
| `backups/` | cópias anteriores a migrations |
| `runtime.lock` | exclusão entre starters |
| `runtime.json` | PID, porta, nonce e identidade do projeto |
| `worktrees/` | worktrees isolados dos runs |

Essa pasta deve permanecer no `.gitignore`. Não copie `runtime.json` para logs ou tickets: nonce e
token da API local são segredos. No POSIX, o diretório usa modo `0700` e os arquivos sensíveis
`0600`; no Windows, a DACL é recriada sem herança ou ACEs de terceiros, deixando somente o SID da
conta atual com controle total, e o resultado é verificado. Se essa restrição não puder ser
aplicada, o runtime falha fechado antes de gravar o token.

Dois starters concorrentes devem convergir para o mesmo runtime. A posse do lock não depende de
`runtime.json`: qualquer PID vivo cuja reutilização não tenha sido comprovada permanece protegido,
mesmo com metadata ausente/corrompida ou health degradado. A recuperação só ocorre após a janela
conservadora quando o PID morreu ou seu horário de início comprova reutilização.

## Instalação e build

No diretório do projeto:

```bash
uv sync --all-groups
cd web
npm ci
npm test
npm run build
cd ..
uv run agentboard --help
```

O build Vite grava diretamente em `src/agentboard/static`, que é o bundle servido e empacotado pelo
runtime. Assim, um build aprovado não pode deixar o backend apontando para assets antigos.

O plugin declara `agentboard mcp` e `agentboard codex-hook` diretamente. Instale o wheel ou o CLI
no ambiente que inicia o Codex antes de habilitar o plugin. O funcionamento normal não depende de
download via `uvx`, de alias `python` ou da ativação manual de uma `.venv`.

Desenvolvimento do dashboard pode usar Vite separadamente, mas o backend e o proxy devem continuar
em loopback:

```bash
uv run agentboard runtime
cd web
npm run dev
```

## Configuração segura

A política canônica é `agentboard.yaml`, versionada no Git. SQLite guarda apenas a revisão aplicada
e o estado operacional; ele não substitui o arquivo.

1. Crie um draft com `config_draft_create`.
2. Valide schema, perfis, limites, caminhos e política Git com `config_validate`.
3. Examine o diff e os avisos.
4. Aplique explicitamente com `config_apply_draft`, usando uma autorização humana curta emitida
   pela sessão do dashboard, além da idempotency key e versão esperada. O ator é derivado da
   credencial e nunca aceito como texto do payload.
5. Confirme a revisão ativa com `config_get`.

Um draft inválido nunca altera a política ativa. Uma revisão nova afeta o momento linearizável do
próximo claim; não muda leases ou runs já iniciados.

## Fluxo operacional

1. Abra o projeto e consulte plano, board e configuração.
2. Aprove uma revisão de plano antes de materializar tasks executáveis.
3. Use o scheduler e reserve uma task com `task_claim`.
4. Faça o spawn do agente pela ferramenta nativa do Codex.
5. Entregue somente ao agente reservado a capability curta retornada pelo claim e vincule o
   identificador nativo com `run_start`.
6. Se o spawn falhar, use `task_release_reservation` para liberar WIP e paths imediatamente.
7. Registre heartbeats e checkpoints objetivos.
8. Envie evidências para verificação e faça review proporcional ao risco.
9. Quando exigido, crie checkpoint e integre somente pelo adapter Git local.

Todas as mutações exigem actor derivado de capability, `idempotency_key` e versão esperada. Uma
capability deve ser curta, armazenada somente por hash, limitada a projeto/task/run/operações e
rejeitada após expiração, revogação ou mudança de lease generation.

## Git exclusivamente local

O adapter pode consultar status/diff, criar branches de integração de plano, worktrees por run,
commits de checkpoint e integrar trabalho aprovado localmente. Antes da integração final ele deve
confirmar a branch-alvo aprovada, target SHA esperado, source SHA revisado, árvore limpa, paths sob
lease e ausência de conflito.

São proibidos:

- `push`, `pull`, `fetch`, `clone` e `ls-remote`;
- GitHub CLI (`gh`), PRs, APIs remotas e deploy;
- `reset` destrutivo e `stash` automático;
- hooks Git de terceiros, filtros externos, LFS e submódulos capazes de iniciar rede;
- integrar arquivos fora do lease ou prosseguir após conflito/target movido.

O hook em `hooks/hooks.json` bloqueia tentativas comuns no shell. Ele é defesa em profundidade:
wrappers ou ferramentas não reconhecidas podem escapar do matcher, portanto não substitui o adapter,
as capabilities, os perfis restritos nem testes de ausência de rede. O `SessionStart` registra em
`.agentboard/hook-protection.json` os hashes do módulo instalado e da configuração de hooks
efetivamente carregada; claims com Git habilitado são recusados se o marcador estiver ausente,
vencido ou se qualquer um desses componentes tiver mudado.

## Dashboard e segurança de localhost

- Bind obrigatório em `127.0.0.1`; nunca use `0.0.0.0` como conveniência.
- Abra o dashboard apenas por `dashboard_open`, usando bootstrap de uso único.
- Após o bootstrap, use cookie `HttpOnly` e `SameSite`; mutações exigem CSRF.
- Rejeite `Host` e `Origin` inesperados, inclusive de páginas públicas tentando acessar localhost.
- Redija tokens, nonce, cookies, headers de autorização e conteúdo sensível dos eventos/logs.
- SSE deve aceitar `Last-Event-ID`; quando o replay não estiver disponível, recarregue um snapshot.

## Backup e migrations

Antes de migration, encerre writers, crie uma cópia SQLite consistente em
`.agentboard/backups/` e registre schema/revisão. Migrations são numeradas e transacionais. Em caso
de falha, preserve o banco anterior e não faça downgrade destrutivo automático.

Uma verificação operacional mínima inclui:

```bash
uv run agentboard --help
uv run pytest
uv run ruff check .
```

Não versionar bancos, WAL/SHM, backups, locks, worktrees, runtime metadata, tokens ou URLs de
bootstrap.

## Recuperação

### Runtime não responde

1. Consulte PID/porta sem expor o nonce.
2. Verifique se o PID corresponde ao projeto e se o health check autenticado responde.
3. Se o processo terminou, preserve banco e logs redigidos, remova somente metadados confirmados
   como abandonados e reinicie a ponte.
4. Se o processo está vivo mas degradado, não inicie um segundo writer; encerre-o de forma normal
   antes da recuperação.

### Lease ou heartbeat expirado

Marque o run como stale pelo serviço. O serviço libera lease/WIP, preserva checkpoints, abandona
qualquer review pendente com motivo auditável, devolve as instâncias à capacidade disponível e
incrementa a geração. Mensagens da geração anterior devem continuar rejeitadas. Um retry começa
com novo claim.

### Conflito Git local

Interrompa a integração, preserve worktree e checkpoint e registre o conflito como evidência. Não
execute reset, stash ou resolução automática. Um humano ou task de rework decide a continuação.

### Banco danificado

Pare o runtime, preserve os arquivos para diagnóstico, verifique a última cópia consistente e
restaure para um novo caminho. Nunca sobrescreva a única cópia. Reabra o projeto e confira event log,
runs ativos e leases antes de aceitar novos claims.

## Verificação do bundle

```bash
python "/path/to/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py" .
agentboard codex-hook --self-test
```

O primeiro comando valida o manifesto e seus componentes; o segundo demonstra decisões esperadas
para comandos Git locais permitidos e operações remotas/destrutivas bloqueadas.
