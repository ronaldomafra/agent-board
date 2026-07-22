# Backlog do Frontend Administrativo MediaFlow

Módulo canônico: `frontend-v2/`. A fonte de estado é [`doc/ORCHESTRATION.md`](../doc/ORCHESTRATION.md); somente `/root` altera status, owner e lease. O frontend anterior está [congelado como legado](../frontend/TASKS.md).

## Gate G0

| ID | Resultado e aceite resumido | Dependências | Evidência / testes | Estado |
| --- | --- | --- | --- | --- |
| `MF-G0-FE2-01` | Lint sem erros ou warnings, sem regressão funcional | — | Integrado em `c9374c8`; `lint --max-warnings=0`, 2 testes e build reverificados | `DONE` |
| `MF-G0-FLAG-01` | Cliente/configuração das flags `BRANDS`, `PUBLIC_SIGNUP` e `PREVIEW`, todas desligadas por padrão | — | Integrado em `f716edb`; backend com 85 testes e frontend com lint zero, 5 testes e build verdes | `DONE` |

## Administração de marcas — G2

| ID | Resultado e aceite resumido | Áreas prováveis | Dependências | Testes mínimos | Tam. | Estado |
| --- | --- | --- | --- | --- | --- | --- |
| `MF-G2-FE2-101` | Tema/tokens/layout MediaFlow aplicados incrementalmente sem alterar regras de negócio | tema, UI compartilhada, shell e telas prioritárias | `G0` | unit/component, visual e responsivo | G | `BACKLOG` |
| `MF-G2-FE2-102` | Menu, rotas e guardas de Marcas refletem ADMIN/RESELLER e flags | router, shell, auth | `MF-G2-FE2-101`, `MF-G1-BE-104` | rotas/RBAC/flag off | M | `BACKLOG` |
| `MF-G2-FE2-103` | Lista e editor salvam draft, tratam dirty state e conflito otimista sem perda silenciosa | pages/api/forms | `MF-G2-FE2-102`, `MF-G1-BE-105` | component/integration e conflito | G | `BACKLOG` |
| `MF-G2-FE2-104` | Upload/seleção mostram validação, progresso, variantes, falha e retry sem URL interna | assets/components/api | `MF-G2-FE2-103`, `MF-G1-BE-114` | upload válido/inválido, RBAC e retry | G | `BACKLOG` |
| `MF-G2-FE2-105` | Histórico/comparação/restauração/aprovação/publicação usam preview real e confirmações | pages/preview/versioning | `MF-G2-FE2-103`, `MF-G2-FE2-104`, `MF-G1-BE-106`, `MF-G1-BE-115` | fluxo vertical, conflitos e estados | G | `BACKLOG` |
| `MF-G2-FE2-106` | ADMIN inspeciona marcas e suspende/reativa com motivo e feedback auditável | pages/admin/api | `MF-G2-FE2-102`, `MF-G1-BE-107` | RBAC, confirmação e erro | M | `BACKLOG` |

## Geração assistida — G4

| ID | Resultado e aceite resumido | Dependências | Testes mínimos | Estado |
| --- | --- | --- | --- | --- |
| `MF-G4-FE2-101` | Wizard inicia job, acompanha progresso e permite revisar/regenerar somente por ação explícita; edição manual não chama IA | `MF-G4-AI-103`, `MF-G4-AI-105` | estados, polling/retry, custo visível e ausência de chamada implícita | `BACKLOG` |

## Qualidade transversal

Cada task deve manter `npm run lint -- --max-warnings=0`, testes e build verdes; cobrir loading/empty/error, teclado, contraste e responsividade no escopo. Contratos novos devem seguir o OpenAPI, sem expor prompts, tokens, storage keys ou credenciais.

O futuro frontend público é um app Next.js SSR separado. `frontend-public/TASKS.md` será criado pela task `MF-G2-PUB-101` junto com o scaffold, evitando um diretório vazio antes da execução.
