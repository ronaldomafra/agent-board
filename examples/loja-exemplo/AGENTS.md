# Regras de desenvolvimento da Loja Modelo

## Objetivo

Manter uma pequena vitrine estática, acessível e responsiva para exercitar o fluxo de
planejamento, execução, evidência e revisão do AgentBoard.

## Leia antes de alterar

1. `docs/PROJECT_BRIEF.md` para entender o escopo e os critérios do exemplo.
2. `README.md` para visualizar e validar o projeto.
3. `agentboard.yaml` para conhecer limites, perfis e políticas operacionais.

## Limites do projeto

- Use apenas HTML semântico e CSS local.
- Não adicione login, carrinho, pagamento, banco de dados ou servidor de aplicação.
- Não adicione bibliotecas, gerenciadores de pacote, fontes, imagens ou serviços externos.
- Mantenha todo o conteúdo visível em português do Brasil.
- Preserve as três rotas estáticas: `index.html`, `produtos.html` e `sobre.html`.
- Mantenha navegação por teclado, foco visível, hierarquia de títulos e contraste legível.
- O site deve funcionar abrindo os arquivos localmente e também por servidor HTTP simples.

## Trabalho com agentes

- Cada agente altera somente os caminhos concedidos pela sua reserva exclusiva.
- Não edite `styles.css` quando outra tarefa já possuir reserva sobre esse arquivo.
- Mudanças compartilhadas de estilo devem ser uma tarefa própria ou depender das tarefas
  que alteram a estrutura das páginas.
- Nunca edite arquivos de estado em `.agentboard/`.
- Toda conclusão deve informar arquivos alterados, validações executadas, evidência de
  aceitação e riscos restantes.
- Uma revisão deve ser feita por agente diferente de quem implementou a tarefa.

## Validação mínima

Execute na raiz deste exemplo:

```powershell
python scripts/verificar_site.py
agentboard validate-config --project .
```

Depois, confira manualmente as três páginas em uma largura ampla e em uma largura móvel.
