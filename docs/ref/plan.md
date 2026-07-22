Analise o repositório completo antes de propor qualquer alteração.

Neste primeiro momento, não implemente código, não altere arquivos e não crie migrations.

Sua tarefa é analisar o estado atual do sistema e criar um plano técnico completo para transformar o projeto em uma plataforma white-label chamada MediaFlow.

Organize o trabalho em épicos, features e tasks menores. Para cada task, informe dependências, riscos, módulos afetados, critérios de aceite e testes necessários.

# 1. Contexto do projeto

O sistema foi desenvolvido inicialmente usando os nomes CineLar e CinePega.

A versão atual está homologada e deve ser considerada a base funcional da nova fase.

A partir desta nova versão:

- MediaFlow será o nome oficial da plataforma.
- MediaFlow será o nome interno do backend e frontend administrativo.
- CinePega deixará de representar a plataforma.
- CinePega será somente um exemplo de marca criada por um revendedor.
- O sistema passará a operar como uma plataforma white-label.
- Cada revendedor poderá criar sua própria marca.
- Cada revendedor poderá ter sua própria página comercial.
- Futuramente, cada revendedor poderá solicitar uma versão personalizada do aplicativo.

O sistema será composto por:

- Backend do MediaFlow.
- Frontend administrativo do MediaFlow.
- Landing page institucional do MediaFlow.
- Gestão de revendedores.
- Gestão de marcas white-label.
- Páginas comerciais dos revendedores.
- Integração com IA por meio da OpenRouter.
- Template genérico do aplicativo.
- Serviço externo responsável pela personalização e compilação do aplicativo.

# 2. Terminologia oficial

Utilize os seguintes termos no planejamento:

- MediaFlow: plataforma principal.
- Administrador da plataforma: responsável pela operação geral do MediaFlow.
- Revendedor: usuário que cria uma operação própria dentro do MediaFlow.
- Cliente final: cliente atendido pelo revendedor.
- Marca white-label: identidade comercial criada pelo revendedor.
- Página comercial: landing page pública de uma marca.
- App template: aplicativo genérico preparado para receber configurações.
- Configuração da marca: conjunto versionado de textos, cores, imagens e configurações.
- Geração de marca: processo de criação ou atualização da identidade com IA.
- Build do aplicativo: processo de aplicar uma configuração ao app template e compilá-lo.

Não utilize “representante” e “revendedor” como entidades diferentes. O termo oficial será “revendedor”.

# 3. Referências disponíveis

Analise as seguintes pastas.

## `docs/mediaflow-landing-reference`

Contém a referência visual da página institucional do MediaFlow.

A página apresenta:

- Proposta da plataforma.
- Benefícios para revendedores.
- Modelo de créditos.
- Pacotes comerciais.
- Botão de login.
- Botão para criação de uma revenda.
- Identidade visual do MediaFlow.

A identidade visual deverá servir como base para a modernização do frontend administrativo.

Não copie o código automaticamente. Primeiro avalie:

- Arquitetura atual do frontend.
- Componentes existentes.
- Sistema de estilos.
- Compatibilidade com o projeto.
- Possibilidade de reutilização.
- Impacto da integração.

## `docs/cinepega-landing-reference`

Contém:

- Uma landing page comercial completa em React/Next.js.
- Um template estático configurável para revendedores.

Esse material deverá ser utilizado como referência para as páginas comerciais white-label.

A página final não poderá conter textos, cores, planos ou referências fixas ao CinePega.

CinePega deverá ser tratado somente como uma configuração de exemplo aplicada ao template do MediaFlow.

# 4. Decisões já aprovadas

Considere como decisões definidas:

1. O nome oficial da plataforma será MediaFlow.
2. O MediaFlow será uma plataforma white-label.
3. Cada revendedor poderá criar sua própria marca.
4. Cada revendedor poderá publicar uma página comercial.
5. As páginas serão hospedadas inicialmente no ambiente do MediaFlow.
6. A identidade visual da landing page do MediaFlow será usada como referência no frontend administrativo.
7. Cores, tipografia, espaçamentos e estilos deverão ser abstraídos em design tokens.
8. Não deverá existir uma cópia manual da página para cada revendedor.
9. O template deverá ser alimentado por uma configuração estruturada.
10. A IA auxiliará na criação da marca, dos textos, das cores e de poucos assets.
11. A integração com os modelos será realizada por meio da OpenRouter.
12. A OpenRouter deverá ser acessada exclusivamente pelo backend.
13. O revendedor terá uma pré-visualização antes de publicar a página.
14. O aplicativo será transformado em um template white-label genérico.
15. A compilação do aplicativo provavelmente será realizada em outro servidor.
16. A comunicação com o serviço externo de build será assíncrona.
17. O serviço externo notificará o MediaFlow por webhook.
18. O serviço externo poderá enviar o código personalizado ao GitHub.
19. A versão homologada não poderá ser quebrada durante a migração.
20. A implementação deverá ser incremental.

# 5. Objetivo do planejamento

Criar um plano técnico para a próxima fase do projeto, contemplando:

- Landing page institucional do MediaFlow.
- Refatoração visual do frontend administrativo.
- Gestão das marcas dos revendedores.
- Geração de identidade com IA.
- Pipeline de imagens e assets.
- Preview da marca e da página.
- Publicação da página comercial.
- Transformação do aplicativo em template.
- Serviço externo de personalização e build.
- Integração assíncrona por webhook.
- Integração do serviço de build com GitHub.
- Controle de versões, custos, segurança e auditoria.

# 6. Diagnóstico obrigatório

Antes de criar o plano, analise o repositório e apresente um diagnóstico contendo:

- Estrutura dos projetos e módulos.
- Tecnologias utilizadas.
- Arquitetura do backend.
- Arquitetura do frontend.
- Arquitetura do aplicativo.
- Rotas existentes.
- Sistema de autenticação.
- Sistema de autorização e permissões.
- Modelos de usuário, revendedor, cliente e créditos.
- Estrutura atual do banco.
- Migrations existentes.
- Endpoints relacionados a OpenRouter ou IA.
- Clientes HTTP já implementados.
- Sistema atual de upload e armazenamento.
- Estrutura de estilos do frontend.
- Componentes compartilhados.
- Sistema de tema existente.
- Cores e textos fixos.
- Referências existentes a CineLar e CinePega.
- Configurações fixas do aplicativo.
- Estrutura atual de build.
- Integrações existentes com GitHub.
- Webhooks já existentes.
- Funcionalidades que podem ser reutilizadas.
- Riscos de regressão.

Não assuma que as estruturas presentes nas páginas de referência já existem no backend.

# 7. Épico 1 — Landing page institucional do MediaFlow

Planejar a integração da landing page presente em:

`docs/mediaflow-landing-reference`

A página deverá conter:

- Proposta comercial da plataforma.
- Benefícios para revendedores.
- Explicação do funcionamento.
- Modelo de créditos.
- Pacotes disponíveis.
- Login.
- Cadastro de revendedor.
- Perguntas frequentes.
- Chamadas comerciais.

O plano deverá identificar:

- Rota da página.
- Local de implementação.
- Componentes reutilizáveis.
- Dados estáticos.
- Dados vindos do backend.
- Integração futura dos botões.
- Responsividade.
- SEO.
- Metadados.
- Estados de carregamento e erro.
- Impacto sobre o frontend atual.

Não considerar os valores demonstrativos da landing page como regras comerciais definitivas.

# 8. Épico 2 — Refatoração visual do frontend administrativo

Planejar a atualização visual do frontend administrativo usando a identidade do MediaFlow.

A refatoração deverá abranger:

- Cores.
- Tipografia.
- Espaçamentos.
- Botões.
- Campos.
- Cards.
- Tabelas.
- Modais.
- Menus.
- Navegação.
- Feedbacks.
- Alertas.
- Estados vazios.
- Estados de erro.
- Carregamentos.
- Responsividade.

Criar ou adaptar um sistema centralizado de design tokens.

Avaliar tokens para:

- Cor primária.
- Cor secundária.
- Cor de destaque.
- Fundo.
- Superfícies.
- Textos.
- Bordas.
- Sucesso.
- Aviso.
- Erro.
- Border radius.
- Sombras.
- Tipografia.
- Espaçamentos.

O plano deverá apresentar:

- Estrutura atual de estilos.
- Componentes duplicados.
- Componentes compartilhados.
- Estratégia de migração gradual.
- Telas prioritárias.
- Impacto da mudança.
- Riscos de regressão visual.
- Testes visuais necessários.

A refatoração não poderá alterar regras de negócio.

# 9. Épico 3 — Gestão da marca do revendedor

Planejar a funcionalidade que permitirá ao revendedor criar e gerenciar sua marca.

Avaliar a necessidade dos seguintes campos:

- Nome da marca.
- Slug público.
- Slogan.
- Descrição.
- Logotipo.
- Favicon.
- Ícone do aplicativo.
- Cor primária.
- Cor secundária.
- Cor de destaque.
- Cores de fundo.
- Cores das superfícies.
- Cores dos textos.
- Tipografia.
- Imagem principal.
- Informações promocionais.
- WhatsApp.
- Telefone.
- E-mail.
- Horário de atendimento.
- Redes sociais.
- Planos comerciais.
- Termos de uso.
- Política de privacidade.
- Metadados de SEO.
- Status da marca.
- Status da publicação.

O plano deverá definir:

- Modelo de dados.
- Relacionamento com o revendedor.
- Validações.
- Permissões.
- Endpoints.
- Armazenamento de assets.
- Versionamento.
- Auditoria.
- Slugs duplicados.
- Configuração padrão.
- Rascunho.
- Aprovação.
- Publicação.
- Despublicação.
- Exclusão.
- Histórico de alterações.

A configuração da plataforma não poderá ser misturada com a configuração das marcas.

# 10. Épico 4 — Integração com OpenRouter

O MediaFlow utilizará a OpenRouter para acessar modelos de IA.

Antes de criar uma integração nova, verifique se já existem:

- Serviços da OpenRouter.
- Endpoints.
- Clientes HTTP.
- Configurações.
- Prompts.
- Modelos de request e response.
- Controle de tokens.
- Tratamento de erros.

Avalie o que pode ser reutilizado ou refatorado.

A integração deverá existir somente no backend.

O frontend nunca deverá receber:

- API key da OpenRouter.
- Tokens privados.
- Prompts internos.
- Credenciais do GitHub.
- Credenciais de armazenamento.
- Credenciais do servidor de build.
- Certificados de assinatura.

Planejar:

- Cliente HTTP.
- Configuração segura.
- Abstração de providers e modelos.
- Modelos de texto.
- Modelos multimodais.
- Geração de imagens.
- Seleção e troca de modelos.
- Timeouts.
- Retry.
- Fallback.
- Rate limiting.
- Limites de tokens.
- Custos.
- Auditoria.
- Observabilidade.
- Histórico.
- Moderação.
- Versionamento de prompts.
- Validação das respostas.
- Tratamento de indisponibilidade.

Não assumir que todos os modelos da OpenRouter geram imagens.

Separar as abstrações de:

- Geração de texto.
- Análise de imagens.
- Geração de imagens.
- Processamento de assets.

# 11. Épico 5 — Criação da marca com IA

A IA ajudará o revendedor a criar uma proposta inicial contendo:

- Nome, quando necessário.
- Slogan.
- Descrição.
- Paleta de cores.
- Tipografia.
- Estilo visual.
- Textos comerciais.
- Conteúdo das seções.
- SEO.
- Logo.
- Favicon.
- Ícone do aplicativo.
- Imagem principal opcional.
- Configuração visual do aplicativo.

Fluxo esperado:

1. O revendedor inicia a criação.
2. Informa dados básicos.
3. Opcionalmente envia uma imagem ou logotipo.
4. O backend cria um job.
5. A IA gera uma proposta estruturada.
6. O backend valida a resposta.
7. O resultado é salvo como rascunho.
8. O frontend apresenta o preview.
9. O revendedor edita, aprova ou solicita nova geração.
10. A versão aprovada pode ser publicada.
11. A configuração aprovada poderá ser usada para gerar o aplicativo.

Dados iniciais sugeridos:

- Nome.
- Segmento.
- Público-alvo.
- Descrição.
- Estilo desejado.
- Preferências de cores.
- Referências visuais.
- Restrições.
- Diferenciais comerciais.

A geração não poderá sobrescrever uma marca publicada automaticamente.

# 12. Resposta estruturada da IA

A IA não deverá retornar código HTML, React, CSS ou Kotlin como resultado principal.

O fluxo deverá ser:

LLM → configuração estruturada → validação → template → preview

Criar um schema versionado semelhante a:

- `schemaVersion`
- `brand`
- `designTokens`
- `typography`
- `content`
- `landingPage`
- `app`
- `assets`
- `seo`
- `generationMetadata`

Os design tokens deverão contemplar:

- `primaryColor`
- `secondaryColor`
- `accentColor`
- `backgroundColor`
- `surfaceColor`
- `textPrimaryColor`
- `textSecondaryColor`
- `successColor`
- `warningColor`
- `errorColor`
- `borderRadius`
- `fontFamily`

O backend deverá:

- Validar o JSON.
- Rejeitar campos desconhecidos.
- Validar cores.
- Limitar textos.
- Sanitizar conteúdo.
- Validar URLs.
- Validar referências de assets.
- Aplicar valores padrão.
- Associar o resultado ao revendedor.
- Manter compatibilidade de schemas.
- Não executar comandos ou caminhos retornados pela IA.

# 13. Épico 6 — Redução de tokens e processamento de imagens

A solução deverá minimizar uploads e consumo de tokens.

Planejar:

- Armazenar a imagem antes de enviar para análise.
- Trabalhar com IDs e URLs internas.
- Não reenviar a mesma imagem.
- Salvar a descrição estruturada da primeira análise.
- Reutilizar essa descrição nas próximas solicitações.
- Redimensionar imagens.
- Comprimir uploads.
- Limitar quantidade e dimensão.
- Gerar thumbnails.
- Calcular hash dos arquivos.
- Detectar duplicações.
- Evitar screenshots completos.
- Enviar somente a configuração ao alterar textos e cores.
- Registrar tokens e custos.
- Estimar custo por geração.
- Limitar regenerações.

A primeira versão deverá priorizar poucos assets:

- Um logotipo.
- Um favicon.
- Um ícone principal.
- Uma imagem principal opcional.
- Uma imagem social opcional.

Não incluir galerias extensas nesta fase.

# 14. Épico 7 — Pipeline de assets

Separar:

1. Geração textual.
2. Geração visual.
3. Processamento técnico.
4. Armazenamento.
5. Aplicação no template.
6. Geração de recursos para o aplicativo.

Os assets deverão passar por um pipeline controlado.

Planejar:

- Validação do MIME type.
- Validação da extensão.
- Limite de tamanho.
- Conversão.
- Redimensionamento.
- Compressão.
- Transparência.
- Geração de variantes.
- Armazenamento.
- Versionamento.
- Exclusão.
- Associação ao revendedor.
- Controle de acesso.
- Checksums.

Para o aplicativo, considerar:

- Ícone principal.
- Ícone adaptativo Android.
- Foreground.
- Background.
- Densidades Android.
- Splash screen.
- Logo interno.
- Recursos iOS, caso existam.

A resposta da IA não deverá ser considerada pronta para inclusão direta no projeto.

# 15. Épico 8 — Preview da marca

O frontend administrativo deverá apresentar uma pré-visualização antes da publicação.

Incluir preview de:

- Landing page desktop.
- Landing page mobile.
- Paleta.
- Tipografia.
- Componentes.
- Botões.
- Campos.
- Ícone.
- Splash screen.
- Tela inicial do aplicativo.

O revendedor deverá poder:

- Editar textos.
- Alterar cores.
- Trocar imagens.
- Restaurar valores.
- Solicitar nova geração.
- Comparar versões.
- Salvar rascunho.
- Aprovar.
- Publicar.
- Solicitar aplicativo.

O preview deverá usar o mesmo renderer e os mesmos design tokens da página real.

Alterações manuais não deverão disparar chamadas para a IA automaticamente.

# 16. Épico 9 — Página comercial white-label

Planejar uma página dinâmica baseada em:

`docs/cinepega-landing-reference`

Cada marca deverá ter uma página própria, sem duplicação de código.

Avaliar:

- Rota por slug.
- Carregamento da configuração.
- Aplicação do tema.
- Conteúdo configurável.
- Planos comerciais.
- Contatos.
- WhatsApp.
- SEO.
- Metadados.
- Cache.
- Responsividade.
- Segurança.
- Isolamento de dados.
- Preview.
- Publicação.
- Marca inexistente.
- Marca inativa.
- Página não publicada.
- Fallback de assets.
- Domínio personalizado futuro.

Nesta fase, identificar quais botões permanecerão sem ação e quais dependerão de pagamentos, cadastro ou testes futuros.

# 17. Estados da geração da marca

Planejar uma máquina de estados.

Sugestão inicial:

- `DRAFT`
- `QUEUED`
- `GENERATING_TEXT`
- `GENERATING_ASSETS`
- `PROCESSING_ASSETS`
- `READY_FOR_REVIEW`
- `APPROVED`
- `PUBLISHED`
- `FAILED`
- `CANCELED`

Adapte os nomes aos padrões existentes.

Registrar:

- Revendedor.
- Marca.
- Usuário solicitante.
- Datas.
- Modelo utilizado.
- Versão do prompt.
- Tokens.
- Custo.
- Duração.
- Tentativas.
- Erros sanitizados.
- Versão da configuração.

# 18. Épico 10 — App template do MediaFlow

Analise o aplicativo atual e planeje sua transformação em um template genérico chamado provisoriamente de:

MediaFlow App Template

Identifique valores fixos relacionados a:

- Nome.
- Application ID.
- Bundle ID.
- Ícones.
- Logo.
- Splash screen.
- Cores.
- Textos.
- URLs.
- API.
- Tema.
- Configurações.
- CinePega.
- CineLar.

O app template deverá receber uma configuração validada contendo:

- Nome.
- Nome curto.
- Identificador solicitado.
- Paleta.
- Logo.
- Ícones.
- Splash.
- URLs.
- Textos.
- Tema.
- ID da marca.
- Versão da configuração.

A LLM não deverá alterar livremente:

- Código-fonte.
- Dependências.
- Permissões.
- Regras de negócio.
- Arquitetura.
- Configurações sensíveis.

O plano deverá analisar:

- Recursos Android.
- Recursos iOS.
- Configurações KMP.
- Tema Compose.
- Build variants.
- Product flavors, se aplicável.
- Cache de assets.
- Validações antes do build.
- Assinatura.
- Versionamento.

# 19. Épico 11 — Serviço externo de build

A compilação provavelmente será executada fora da VPS principal.

O serviço externo será responsável por:

1. Receber uma solicitação.
2. Validar a autenticidade.
3. Obter a versão do app template.
4. Obter a configuração aprovada.
5. Aplicar textos, cores e assets.
6. Gerar os recursos técnicos.
7. Compilar.
8. Executar testes.
9. Armazenar o artefato.
10. Enviar o código ao GitHub, quando aplicável.
11. Notificar o MediaFlow.
12. Disponibilizar logs sanitizados.

O plano deverá comparar:

- Branch por revendedor.
- Branch por build.
- Repositório por revendedor.
- Repositório por aplicativo.
- Apenas artefato, sem persistência do código gerado.

Apresente vantagens, riscos, custos e recomendação.

# 20. Comunicação com o serviço de build

A integração deverá ser assíncrona.

O MediaFlow enviará uma solicitação e receberá um ID de job.

O serviço externo notificará por webhook:

- Início.
- Progresso relevante.
- Sucesso.
- Falha.
- Cancelamento.

O payload deverá considerar:

- ID do job.
- ID do revendedor.
- ID da marca.
- Versão da configuração.
- Versão do template.
- Status.
- Timestamp.
- Commit.
- URL do artefato.
- Checksum.
- Mensagem de erro sanitizada.

Planejar segurança com:

- HTTPS.
- HMAC.
- Timestamp.
- Proteção contra replay.
- Idempotency key.
- Rotação de secrets.
- Validação do payload.
- Rate limiting.
- Auditoria.
- Processamento idempotente.

O MediaFlow deverá validar:

- Assinatura.
- Timestamp.
- Estado atual.
- Versão da configuração.
- Checksum.
- Identidade do job.

# 21. Estados do build

Planejar uma máquina de estados independente.

Sugestão:

- `REQUESTED`
- `QUEUED`
- `PREPARING_TEMPLATE`
- `GENERATING_RESOURCES`
- `COMPILING`
- `TESTING`
- `UPLOADING_ARTIFACT`
- `PUSHING_TO_GIT`
- `COMPLETED`
- `FAILED`
- `CANCELED`

Registrar:

- Template utilizado.
- Configuração utilizada.
- Commit base.
- Commit final.
- Ambiente.
- Duração.
- Logs.
- Artefatos.
- Checksums.
- Tentativas.
- Erros.
- Usuário solicitante.

# 22. Épico 12 — Integração com GitHub

Planejar:

- Uso de GitHub App ou credencial de escopo mínimo.
- Estratégia de repositórios.
- Estratégia de branches.
- Convenção de nomes.
- Commits rastreáveis.
- Associação entre build e commit.
- Proteção de branches.
- Retenção.
- Limpeza.
- Tratamento de falha no push.
- Confirmação do commit.
- Proteção de secrets.

Não utilizar tokens pessoais amplos e permanentes.

Não armazenar no GitHub:

- API keys.
- Certificados.
- Keystores.
- Senhas.
- Secrets de webhook.
- Tokens de assinatura.

Tratar build, upload do artefato e push para o GitHub como etapas independentes.

# 23. Divisão inicial em features

Organize o plano pelo menos nas seguintes features:

## Feature A — Fundação white-label

- Padronização MediaFlow.
- Separação entre plataforma e marca.
- Modelos base.
- Permissões.
- Migração gradual.

## Feature B — Landing page institucional

- Integração da referência.
- Rotas.
- Componentes.
- Login e cadastro.
- SEO.

## Feature C — Design system administrativo

- Design tokens.
- Componentes.
- Tema MediaFlow.
- Migração visual.

## Feature D — Gestão de marcas

- CRUD.
- Slug.
- Configuração.
- Versionamento.
- Publicação.

## Feature E — OpenRouter

- Cliente.
- Modelos.
- Prompts.
- Custos.
- Segurança.
- Observabilidade.

## Feature F — Geração de marca com IA

- Formulário.
- Jobs.
- Schema.
- Histórico.
- Aprovação.

## Feature G — Pipeline de assets

- Upload.
- Geração.
- Processamento.
- Armazenamento.
- Variantes.

## Feature H — Preview

- Desktop.
- Mobile.
- App.
- Edição.
- Comparação.

## Feature I — Página comercial

- Renderer.
- Tema.
- Slug.
- SEO.
- Publicação.

## Feature J — App template

- Parametrização.
- Recursos.
- Tema.
- Configuração de build.

## Feature K — Orquestração de builds

- API.
- Fila.
- Jobs.
- Webhook.
- Retentativas.
- Cancelamento.

## Feature L — GitHub e artefatos

- Push.
- Commits.
- Repositórios.
- Armazenamento.
- Download.
- Histórico.

# 24. Formato esperado do plano

Organize a resposta em:

1. Resumo executivo.
2. Diagnóstico do estado atual.
3. Problemas encontrados.
4. Decisões arquiteturais recomendadas.
5. Épicos.
6. Features.
7. Tasks.
8. Dependências.
9. Ordem de execução.
10. Modelos e migrations.
11. Endpoints.
12. Telas e componentes.
13. Jobs assíncronos.
14. Webhooks.
15. Segurança.
16. Testes.
17. Observabilidade.
18. Riscos.
19. Itens fora do escopo.
20. Dúvidas pendentes.

Para cada task, informe:

- Título.
- Objetivo.
- Contexto.
- Escopo.
- Módulos afetados.
- Arquivos provavelmente afetados.
- Dependências.
- Resultado esperado.
- Critérios de aceite.
- Testes.
- Riscos.
- Estimativa relativa: pequena, média ou grande.

# 25. Ordem recomendada

Avalie e ajuste esta ordem:

1. Diagnóstico.
2. Padronização dos conceitos.
3. Fundação multi-tenant e white-label.
4. Design system.
5. Gestão manual de marcas.
6. Página comercial dinâmica.
7. Preview.
8. Integração OpenRouter.
9. Geração de textos e design tokens.
10. Pipeline de imagens.
11. Geração completa da marca.
12. Transformação do aplicativo em template.
13. Serviço externo de build.
14. Webhooks.
15. GitHub e distribuição.

A gestão manual da marca deverá funcionar antes de depender da IA.

# 26. Fora do escopo inicial

Não incluir como implementação imediata, salvo se já existir uma base reutilizável:

- Publicação automática na Play Store.
- Publicação automática na App Store.
- Domínios personalizados.
- Compra automática de créditos.
- Pagamentos definitivos.
- Assinatura automatizada sem definição segura dos certificados.
- Alterações livres no código por LLM.
- Criação de um aplicativo completamente diferente por revendedor.
- Galerias extensas geradas por IA.
- IA de recomendação para clientes finais.

Esses itens podem aparecer como fases futuras.

# 27. Restrições

- Não implementar código nesta etapa.
- Não alterar a versão homologada.
- Não criar migrations agora.
- Não renomear módulos sem analisar impacto.
- Não assumir regras inexistentes.
- Não considerar valores demonstrativos como definitivos.
- Não misturar dados da plataforma com dados do revendedor.
- Não duplicar páginas por marca.
- Não expor credenciais no frontend.
- Não executar conteúdo retornado pela IA.
- Não confiar em URLs ou arquivos retornados pela IA sem validação.
- Não armazenar secrets no GitHub.
- Não permitir acesso de um revendedor aos dados de outro.
- Não usar CinePega como nome interno da plataforma.
- Preservar CinePega somente como exemplo de marca.

# 28. Decisões que precisam ser apresentadas

Analise e recomende opções para:

1. Estratégia multi-tenant.
2. Modelo de marcas.
3. Schema da configuração.
4. Versionamento do schema.
5. Armazenamento dos assets.
6. Modelos da OpenRouter.
7. Geração de imagens.
8. Controle de custos.
9. Limites por revendedor.
10. Regenerações.
11. Preview.
12. Publicação.
13. Cache.
14. Slugs.
15. Domínios futuros.
16. Parametrização do aplicativo.
17. Estratégia do servidor de build.
18. Estratégia de GitHub.
19. Armazenamento dos artefatos.
20. Assinatura dos aplicativos.
21. Certificados.
22. Secrets.
23. Retentativas.
24. Cancelamento.
25. Builds duplicados.
26. Retenção de logs.
27. Auditoria.

Ao final, liste claramente todas as perguntas que precisam ser respondidas antes de iniciar a implementação.

Não inicie a codificação até que o plano seja revisado e aprovado.
