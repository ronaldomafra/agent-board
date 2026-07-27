"""Verificação autônoma dos arquivos estáticos da Loja Modelo."""

from __future__ import annotations

import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

RAIZ = Path(__file__).resolve().parent.parent
PAGINAS = ("index.html", "produtos.html", "sobre.html")
ARQUIVOS_OBRIGATORIOS = (
    *PAGINAS,
    "styles.css",
    "README.md",
    "AGENTS.md",
    "agentboard.yaml",
    "docs/PROJECT_BRIEF.md",
    "docs/agentboard-plan.example.json",
    ".codex/agents/orchestrator.toml",
    ".codex/agents/worker.toml",
    ".codex/agents/reviewer.toml",
)


class DocumentoHTML(HTMLParser):
    """Coleta a estrutura necessária sem bibliotecas de terceiros."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lang = ""
        self.titulos_documento = 0
        self.h1 = 0
        self.main = 0
        self.ids: list[str] = []
        self.referencias: list[tuple[str, str]] = []
        self.pagina_atual = 0
        self.links_salto = 0

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        atributos = {nome: valor or "" for nome, valor in attrs}
        if tag == "html":
            self.lang = atributos.get("lang", "")
        elif tag == "title":
            self.titulos_documento += 1
        elif tag == "h1":
            self.h1 += 1
        elif tag == "main":
            self.main += 1

        identificador = atributos.get("id")
        if identificador:
            self.ids.append(identificador)

        for atributo in ("href", "src"):
            referencia = atributos.get(atributo)
            if referencia:
                self.referencias.append((atributo, referencia))

        if tag == "a" and atributos.get("aria-current") == "page":
            self.pagina_atual += 1
        if tag == "a" and atributos.get("href") == "#conteudo":
            self.links_salto += 1


def carregar_documentos(erros: list[str]) -> dict[str, DocumentoHTML]:
    documentos: dict[str, DocumentoHTML] = {}
    for nome in PAGINAS:
        caminho = RAIZ / nome
        analisador = DocumentoHTML()
        try:
            analisador.feed(caminho.read_text(encoding="utf-8"))
        except (OSError, UnicodeError) as erro:
            erros.append(f"{nome}: não foi possível ler como UTF-8 ({erro})")
            continue
        documentos[nome] = analisador
    return documentos


def validar_estrutura(
    documentos: dict[str, DocumentoHTML], erros: list[str]
) -> None:
    for nome, documento in documentos.items():
        verificacoes = {
            "idioma deve ser pt-BR": documento.lang == "pt-BR",
            "deve existir exatamente um title": documento.titulos_documento == 1,
            "deve existir exatamente um h1": documento.h1 == 1,
            "deve existir exatamente um main": documento.main == 1,
            "deve existir um link para pular ao conteúdo": documento.links_salto == 1,
            "deve existir exatamente um aria-current": documento.pagina_atual == 1,
            "não deve haver identificadores duplicados": len(documento.ids)
            == len(set(documento.ids)),
        }
        for mensagem, valido in verificacoes.items():
            if not valido:
                erros.append(f"{nome}: {mensagem}")


def validar_referencia(
    pagina: str,
    atributo: str,
    referencia: str,
    documentos: dict[str, DocumentoHTML],
    erros: list[str],
) -> None:
    partes = urlsplit(referencia)
    if partes.scheme or partes.netloc or referencia.startswith("//"):
        erros.append(f"{pagina}: referência externa proibida em {atributo}={referencia!r}")
        return

    caminho_texto = unquote(partes.path)
    destino_nome = caminho_texto or pagina
    destino = (RAIZ / destino_nome).resolve()
    try:
        destino.relative_to(RAIZ)
    except ValueError:
        erros.append(f"{pagina}: referência sai do projeto em {referencia!r}")
        return

    if not destino.is_file():
        erros.append(f"{pagina}: arquivo não encontrado em {referencia!r}")
        return

    if partes.fragment and destino.suffix.lower() == ".html":
        documento_destino = documentos.get(destino.name)
        if documento_destino is None or partes.fragment not in documento_destino.ids:
            erros.append(f"{pagina}: âncora não encontrada em {referencia!r}")


def validar_links(
    documentos: dict[str, DocumentoHTML], erros: list[str]
) -> None:
    for pagina, documento in documentos.items():
        for atributo, referencia in documento.referencias:
            validar_referencia(
                pagina, atributo, referencia, documentos, erros
            )


def validar_ausencia_de_rede(erros: list[str]) -> None:
    padrao_remoto = re.compile(r"(?:https?:)?//", flags=re.IGNORECASE)
    for caminho in [*(RAIZ / nome for nome in PAGINAS), RAIZ / "styles.css"]:
        try:
            conteudo = caminho.read_text(encoding="utf-8")
        except OSError:
            continue
        if padrao_remoto.search(conteudo):
            erros.append(f"{caminho.name}: foi encontrada uma referência de rede")


def main() -> int:
    erros: list[str] = []
    for nome in ARQUIVOS_OBRIGATORIOS:
        if not (RAIZ / nome).is_file():
            erros.append(f"arquivo obrigatório ausente: {nome}")

    documentos = carregar_documentos(erros)
    validar_estrutura(documentos, erros)
    validar_links(documentos, erros)
    validar_ausencia_de_rede(erros)

    if erros:
        print("Falha na verificação do site:")
        for erro in erros:
            print(f"- {erro}")
        return 1

    print(
        "Site verificado sem erros: "
        f"{len(PAGINAS)} páginas, links locais e estrutura básica válidos."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
