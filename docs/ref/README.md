# Backlog operacional MediaFlow

Esta pasta oferece uma visão estática e somente leitura do desenvolvimento da nova versão do MediaFlow.

## Fontes e governança

- `docs/plan.md` é a fonte funcional primária.
- `tasks/backlog-data.js` é a fonte operacional derivada: contém sequência, dependências e estado corrente das 62 tasks canônicas.
- Somente `/root` atualiza `backlog-data.js`, depois de revisar evidências, testes e commit.
- Os arquivos `TASKS*.md` existentes são somente leitura. Seus apêndices CineLar/CinePega são históricos e não entram neste backlog operacional.
- `tasks/index.html` apenas apresenta e valida os dados. A página não edita nem grava status.

Fronteiras de frontend da nova versão:

- `frontend-v2/` é o único frontend administrativo atual.
- `frontend/` é legado e fica explicitamente fora das novas tasks.
- `frontend-public/` é um módulo separado e só será criado por `MF-G2-PUB-101`.

## Como abrir

1. Abra a pasta `tasks` no gerenciador de arquivos.
2. Dê duplo clique em `index.html`.
3. O dashboard carrega `backlog-data.js` automaticamente, desde que ambos permaneçam na mesma pasta.

Não é necessário iniciar nenhum processo auxiliar. Se o arquivo de dados não estiver disponível, a página mostra uma mensagem clara.

## Fluxo de execução

O limite é **WIP 1**, contando `IN_PROGRESS` e `VERIFYING`. Cada task é executada por um único subagente, em branch/worktree próprio e com lease de arquivos sem sobreposição.

Fluxo normal:

`BACKLOG → READY → IN_PROGRESS → VERIFYING → DONE`

Estados de exceção:

- `REWORK`: a revisão encontrou correções; retorna à execução mantendo o mesmo escopo.
- `BLOCKED`: existe impedimento objetivo e registrado.
- `CANCELED`: a task foi retirada explicitamente do escopo.

Uma task só fica `READY` quando:

- todas as dependências exigidas estão `DONE` ou uma exceção local foi aprovada e registrada;
- o aceite e os testes mínimos estão claros;
- o lease está livre e não sobrepõe outro trabalho;
- o WIP permite iniciar uma nova execução;
- o subagente e o SHA-base foram definidos.

O subagente não altera o estado operacional. Ao terminar, entrega commit atômico e relatório com task, base, commit, arquivos, testes, aceite e riscos. `/root` revisa, solicita `REWORK` quando necessário e só então integra e marca `DONE`. Tasks de risco elevado recebem verificação independente.

## Prompt padrão para um subagente

```text
Execute <TASK_ID> a partir de <BASE_SHA> no branch agent/<task-id>.
Lease exclusivo: <ARQUIVOS_OU_DIRETÓRIOS>.
Implemente somente o aceite descrito, usando mocks, fakes, fixtures e recursos locais.
Não altere trackers nem backlog-data.js.
Rode os testes proporcionais ao risco e faça um commit atômico.
Retorne: task_id, outcome, base_sha, commit_sha, changed_files, tests, acceptance e risks.
```

## Política local

Toda implementação e verificação ocorre no ambiente de desenvolvimento local. Integrações futuras são representadas por contratos, adapters, mocks, fakes, fixtures, processos locais e repositórios temporários. Credenciais reais, publicação remota e chamadas de rede ficam fora deste fluxo.

## Crosswalk do plano funcional

| Feature | Intenção coberta | Gates principais | Famílias de tasks |
| --- | --- | --- | --- |
| A | Fundação white-label, modelos, permissões e migração gradual | G0, G1, G3 | `MF-G0-*`, `MF-G1-BE-101..104`, validações G3 |
| B | Landing institucional, cadastro, pacotes e SEO | G0, G1, G2, G3 | `MF-G1-BE-108..111`, `MF-G2-PUB-101..103`, QA G3 |
| C | Tokens, tema e migração visual administrativa | G0, G2, G3 | `MF-G0-FE2-01`, `MF-G2-FE2-101..102`, QA visual |
| D | CRUD, slug, versionamento, publicação e moderação | G0, G1, G2, G3 | `MF-G1-BE-101..107`, `MF-G2-FE2-102..106` |
| E | Adapters OpenRouter, custos, segurança e observabilidade | G0, G3, G4 | `MF-G4-AI-101..105`, `MF-G4-QA-101` |
| F | Jobs e geração assistida de marca com revisão explícita | G0, G3, G4 | `MF-G4-AI-102..104`, `MF-G4-FE2-101`, QA G4 |
| G | Upload, processamento, armazenamento e variantes | G0, G1, G2, G3, G4, G5 | `MF-G1-OPS-101`, `MF-G1-BE-112..114`, tasks de assets |
| H | Preview, comparação, edição e paridade de renderer | G0, G1, G2, G3 | `MF-G1-BE-115`, `MF-G2-FE2-105`, QA G3 |
| I | Página comercial por slug, planos, CTA e SEO | G0, G1, G2, G3 | `MF-G1-BE-107/110/114`, `MF-G2-PUB-104..105` |
| J | App template, configuração e recursos determinísticos | G0, G3, G5, G6 | `MF-G5-APP-101..105`, `MF-G5-QA-101` |
| K | API, worker, jobs, webhook, retry e cancelamento de build | G0, G3, G6 | `MF-G6-BE-*`, `MF-G6-BLD-*`, QA G6 |
| L | Git, commits e ciclo de artefatos | G0, G3, G6 | `MF-G6-GH-*`, `MF-G6-ART-101`, `MF-G6-QA-101` |

O crosswalk é uma visão resumida. A lista exata e imutável das tasks usadas pelo dashboard permanece em `backlog-data.js`.
