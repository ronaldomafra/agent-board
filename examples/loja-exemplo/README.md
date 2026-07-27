# Loja Modelo

Projeto estático e fictício para a primeira rodada de testes do AgentBoard com Codex.
A vitrine tem três páginas responsivas, não usa login nem backend e não faz qualquer
requisição externa.

Instale o plugin seguindo o
[guia de instalação local](../../docs/PLUGIN_INSTALLATION.md) antes de iniciar o cenário.

## O que está incluído

- `index.html`: página inicial com apresentação, diferenciais e produtos em destaque.
- `produtos.html`: grade com seis produtos e preços fictícios.
- `sobre.html`: história, valores e contexto do projeto demonstrativo.
- `styles.css`: estilos compartilhados, responsivos e sem bibliotecas externas.
- `scripts/verificar_site.py`: verificação local de arquivos, links e estrutura básica.
- `agentboard.yaml`: política local pronta para validação pelo AgentBoard.
- `.codex/agents/`: perfis mínimos de orquestração, execução e revisão.
- `docs/PROJECT_BRIEF.md`: escopo e critérios canônicos do exemplo.
- `docs/agentboard-plan.example.json`: argumentos de exemplo para a ferramenta
  `plan_draft_create`.

## Visualização

Não há etapa de compilação. Na raiz desta pasta, inicie um servidor local:

```powershell
python -m http.server 8000
```

Abra `http://127.0.0.1:8000/` no navegador. Encerre o servidor com `Ctrl+C`.
Também é possível abrir `index.html` diretamente, mas o servidor local representa melhor
o uso normal de um site.

## Validação

A verificação do site usa somente a biblioteca padrão do Python:

```powershell
python scripts/verificar_site.py
```

Com o AgentBoard instalado, valide também a política:

```powershell
agentboard validate-config --project .
```

Resultados esperados:

- o verificador termina com `Site verificado sem erros`;
- o AgentBoard retorna `"valid": true`;
- nenhuma página tenta carregar fontes, imagens, scripts ou folhas de estilo remotas;
- os links de Início, Produtos e Sobre funcionam em todas as páginas.

## Critérios de aceitação por página

### Página inicial

- Exibe proposta da marca, dois caminhos principais e três diferenciais.
- Apresenta três produtos em destaque e aponta para a coleção completa.
- Mantém leitura e navegação confortáveis em telas pequenas.

### Produtos

- Exibe exatamente seis produtos demonstrativos com categoria, nome, descrição e preço.
- Informa claramente que não existe carrinho ou processo de compra.
- Organiza a grade em três, duas ou uma coluna conforme o espaço disponível.

### Sobre

- Explica que a marca e a história são fictícias.
- Apresenta os valores Clareza, Cuidado e Simplicidade.
- Registra o limite técnico do exemplo: sem login, backend ou dependências externas.

### Critérios compartilhados

- Cada documento possui um único `h1`, região `main` e título de página.
- A navegação identifica a página atual com `aria-current="page"`.
- O link “Pular para o conteúdo” e os estados de foco são visíveis pelo teclado.
- O conteúdo continua utilizável com redução de movimento ativada.

## Cenário sugerido para o AgentBoard

Para exercitar alterações reais sem destruir esta referência, copie a pasta para um diretório
limpo, inicialize um repositório Git e registre um primeiro commit:

```powershell
git init -b main
git add .
git commit -m "Base da Loja Modelo"
```

Em seguida, use `docs/agentboard-plan.example.json` como referência dos argumentos da
ferramenta MCP `plan_draft_create`. O arquivo não é importado automaticamente. Valide o
rascunho no AgentBoard e aprove-o explicitamente antes de executar.

O plano propõe três tarefas independentes e com reservas sem sobreposição:

1. melhorar a mensagem principal em `index.html`;
2. enriquecer as informações de catálogo em `produtos.html`;
3. acrescentar perguntas frequentes em `sobre.html`.

As três tarefas podem ser executadas em paralelo porque não alteram `styles.css`. Ao final,
cada uma deve rodar `python scripts/verificar_site.py`, registrar evidência e seguir para
revisão. Em uma nova cópia limpa, o mesmo cenário pode ser adaptado para recriar cada página
do zero, mantendo uma página por reserva.

O payload usa o perfil de evidência `default` para simplificar o primeiro smoke test. Depois que
o fluxo básico estiver aprovado e os hooks estiverem confiáveis, troque-o por `code_with_git` em
uma nova revisão para exercitar checkpoints e integração Git estritamente locais.

## Escopo deliberadamente ausente

Este exemplo não inclui carrinho, autenticação, pagamentos, formulários funcionais,
persistência, telemetria, analytics, implantação ou integração remota. Esses itens estão fora
da primeira rodada de testes.
