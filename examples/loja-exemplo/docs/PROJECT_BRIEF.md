# Resumo do projeto — Loja Modelo

## Visão

A Loja Modelo é uma marca varejista fictícia criada para demonstrar um projeto web pequeno,
completo e apropriado para a primeira execução do AgentBoard com agentes Codex.

## Objetivo

Entregar uma vitrine clara e responsiva com três páginas estáticas:

- uma página inicial que apresente a proposta da marca;
- uma página de produtos que mostre um catálogo curto;
- uma página sobre que explique a história e os valores fictícios.

## Público demonstrativo

Pessoas que desejam conhecer uma pequena coleção de objetos para casa e rotina. Não existem
clientes reais, transações ou coleta de dados.

## Requisitos funcionais

1. A navegação principal deve ligar as três páginas entre si.
2. A página inicial deve apresentar a marca, diferenciais e produtos em destaque.
3. A página de produtos deve apresentar seis itens fictícios.
4. A página sobre deve explicar a proposta e os valores da marca.
5. Todas as páginas devem compartilhar identidade visual e rodapé.

## Requisitos de qualidade

- HTML semântico, um `h1` por página e regiões identificáveis.
- Navegação completa por teclado e indicador de foco visível.
- Layout responsivo sem rolagem horizontal nas larguras usuais.
- Conteúdo integralmente local, sem CDN, fontes, imagens ou scripts externos.
- Funcionamento sem JavaScript e sem etapa de compilação.
- Validação local executável apenas com a biblioteca padrão do Python.

## Fora do escopo

- Login, conta de cliente e autorização.
- Carrinho, estoque, pagamento e finalização de compra.
- Backend, banco de dados e API.
- Busca, filtros interativos e painel administrativo.
- Formulários que enviem dados.
- Analytics, telemetria, implantação e integrações remotas.

## Aceitação

O projeto é aceito quando:

1. `python scripts/verificar_site.py` termina sem erros;
2. `agentboard validate-config --project .` aceita a configuração;
3. Início, Produtos e Sobre podem ser alcançadas a partir de qualquer página;
4. o site permanece legível em 360 px de largura;
5. nenhuma página inicia requisição para recurso externo;
6. a Loja Modelo é identificada como uma marca fictícia.

## Primeira rodada no AgentBoard

O arquivo `agentboard-plan.example.json` representa argumentos válidos da ferramenta MCP
`plan_draft_create`. O cenário separa as páginas em três reservas independentes, sem alteração
do CSS compartilhado. Ele foi projetado para testar planejamento, aprovação humana, reserva,
execução paralela, evidência e revisão.
