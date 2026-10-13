"""
Marca ParraDibiei - fonte unica do mascote e da assinatura.

Por que um modulo so: o desenho aparece no painel (cabecalho e rodape), no
dashboard HTML do caso e no indice de casos. Duplicar 40 linhas de SVG em cada
lugar garantiria que um dia tres versoes diferentes conviveriam.

Por que SVG inline e nao arquivo de imagem: o painel roda offline, a partir da
pasta portatil do EMPACOTAR.py, e o dashboard e enviado por e-mail / anexado no
chamado. Um <img src="..."> quebraria nos tres casos. Vetor tambem nao borra em
tela HiDPI.

As cores: o vermelho sai de `var(--brand)` para acompanhar o tema claro/escuro
das telas que ja definem essa variavel. O contorno escuro e literal de proposito
- ele fica sobre o vermelho e o branco do proprio desenho, que nao mudam com o
tema; usar variavel ali apagaria o traco no modo escuro.
"""
from __future__ import annotations

import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Onde as imagens da apresentacao podem ser colocadas. O deck NAO depende
# delas: se a pasta estiver vazia, a marca e o banner sao desenhados com formas
# nativas do PowerPoint (ver `pptx_mascote` e `pptx_banner_oracle`). Assim o
# arquivo abre igual numa maquina sem os assets - que e a regra do projeto para
# tudo que circula por e-mail.
ASSETS_DIR = os.path.join(BASE_DIR, "assets")

# Nomes aceitos, em ordem de preferencia. PNG antes de JPG porque logo com
# fundo transparente e o que encaixa sobre o cartao claro do slide; SVG fica de
# fora de proposito - o python-pptx nao embute SVG, e prometer suporte a um
# formato que quebra na hora de gerar seria pior que nao aceitar.
LOGO_ARQUIVOS = ("parradibiei.png", "parradibiei.jpg", "logo.png", "logo.jpg")
BANNER_ARQUIVOS = ("oracle_banner.png", "oracle_banner.jpg", "oracle.png",
                   "oracle.jpg", "banner.png", "banner.jpg")


def imagem(*nomes: str) -> str:
    """Caminho da primeira imagem que existir em `assets/`, ou '' se nenhuma.

    Devolver '' em vez de levantar erro e proposital: o chamador cai no desenho
    vetorial. Deck que falha por falta de arquivo de imagem e deck que nao sai
    na vespera da apresentacao.
    """
    for nome in nomes:
        p = os.path.join(ASSETS_DIR, nome)
        if os.path.isfile(p):
            return p
    return ""


def logo_arquivo() -> str:
    """Logo ParraDibiei em imagem, se o time colocou uma em `assets/`."""
    return imagem(*LOGO_ARQUIVOS)


def banner_arquivo() -> str:
    """Banner Oracle em imagem, se houver um em `assets/`."""
    return imagem(*BANNER_ARQUIVOS)


# ── marca em PPTX ─────────────────────────────────────────────
# O painel e o dashboard usam o SVG acima; o PowerPoint nao embute SVG, e
# converter exigiria dependencia nova (cairosvg) num ambiente corporativo que
# instala pacote por Artifactory. Entao o deck redesenha o mascote com FORMAS
# nativas - mesmo desenho, mesma paleta, e vetor de verdade: nao borra no
# projetor nem ao imprimir, e o arquivo continua abrindo numa maquina sem nada
# instalado.
def pptx_mascote(k, slide, left: float, top: float, tam: float,
                 vermelho: str = "EC0000", escuro: str = "0F172A") -> None:
    """Mascote ParraDibiei desenhado com formas, ocupando `tam` polegadas."""
    forma, SHAPE = k["forma"], k["SHAPE"]
    # rosto: o "O" vermelho com miolo branco
    forma(slide, SHAPE.ROUNDED_RECTANGLE, left, top + tam * 0.10,
          tam * 0.66, tam * 0.62, vermelho, escuro, ajustes=(0.42,))
    forma(slide, SHAPE.ROUNDED_RECTANGLE, left + tam * 0.10,
          top + tam * 0.20, tam * 0.46, tam * 0.40, "FFFFFF", escuro,
          ajustes=(0.40,))
    # olhos (o direito e a piscadela: mais estreito)
    forma(slide, SHAPE.OVAL, left + tam * 0.18, top + tam * 0.28,
          tam * 0.08, tam * 0.11, escuro)
    forma(slide, SHAPE.OVAL, left + tam * 0.38, top + tam * 0.31,
          tam * 0.09, tam * 0.05, escuro)
    # boca aberta com a lingua vermelha
    forma(slide, SHAPE.OVAL, left + tam * 0.20, top + tam * 0.44,
          tam * 0.26, tam * 0.11, escuro)
    forma(slide, SHAPE.OVAL, left + tam * 0.28, top + tam * 0.49,
          tam * 0.11, tam * 0.05, vermelho)
    # cilindro de banco ao lado do rosto - o que diz "isto e sobre banco"
    forma(slide, SHAPE.FLOWCHART_MAGNETIC_DISK, left + tam * 0.70,
          top + tam * 0.30, tam * 0.28, tam * 0.34, vermelho, escuro)


def pptx_assinatura(k, slide, left: float, top: float, tam: float = 0.62,
                    cor_nome: str = "0F172A", vermelho: str = "EC0000",
                    corpo: float = 17, larg_texto: float = 0.0) -> float:
    """Assinatura da marca no slide. Devolve a largura total usada.

    Dois caminhos, e eles NAO desenham a mesma coisa:

    - com o logo em `assets/`, a arte ja e a assinatura completa (mascote +
      nome + slogan) e entra sozinha;
    - sem arquivo, o mascote e desenhado com formas do PowerPoint e o nome vem
      ao lado, em texto, porque o desenho nao tem letra nenhuma.

    O deck nunca depende do arquivo: sem ele, sai igual em qualquer maquina -
    que e a regra do projeto para tudo que circula por e-mail.
    """
    arq = logo_arquivo()
    if arq:
        # O logo oficial e um LOCKUP: mascote, o nome 'ParraDibiei' e o slogan
        # ja vem dentro da arte. Escrever o nome ao lado - que e o certo para o
        # mascote desenhado, que nao tem texto nenhum - imprimia a marca DUAS
        # vezes, lado a lado, em todo slide do deck.
        pic = slide.shapes.add_picture(arq, k["Inches"](left),
                                       k["Inches"](top),
                                       height=k["Inches"](tam))
        # A largura vem da arte, nao de um palpite. O 1.15 fixo que estava aqui
        # supunha um logo mais largo que alto; o nosso e quadrado, e a conta
        # reservava 15% de espaco vazio antes do que viesse depois.
        return pic.width / 914400.0

    pptx_mascote(k, slide, left, top, tam, vermelho)
    larg_logo = tam
    x = left + larg_logo + 0.10
    # A caixa acompanha o corpo da fonte. Largura fixa era o que fazia a
    # assinatura pequena do cabecalho vazar pela borda direita do slide: 2,4
    # polegadas de caixa para um texto de 1.
    larg_texto = larg_texto or max(0.9, len("ParraDibiei") * corpo * 0.0115)
    tb = k["texto"](slide, x, top + tam * 0.22, larg_texto, tam * 0.6,
                    "Parra", corpo, True, cor_nome)
    # 'Dibiei' em vermelho no MESMO paragrafo: duas caixas de texto separadas
    # desalinhariam com a fonte do PowerPoint do banco, que nem sempre e a
    # mesma medida da nossa.
    run = tb.text_frame.paragraphs[0].add_run()
    run.text = "Dibiei"
    run.font.size = k["Pt"](corpo)
    run.font.bold = True
    run.font.name = "Segoe UI"
    from pptx.dml.color import RGBColor
    run.font.color.rgb = RGBColor.from_string(vermelho)
    return larg_logo + 0.10 + larg_texto


# Vermelho da marca Oracle. Fica aqui, ao lado da nossa cor, para ninguem
# escolher um vermelho "parecido" no meio de um slide.
ORACLE_VERMELHO = "C74634"


def pptx_banner_oracle(k, slide, left: float, top: float, larg: float,
                       alt: float = 0.52, texto_dir: str = "") -> None:
    """Faixa 'Oracle Database' - imagem de `assets/` ou desenhada.

    Sobre a imagem: o logo oficial da Oracle e marca registrada dela e nao esta
    versionado neste repositorio. Quando o time colocar o arquivo em
    `assets/oracle_banner.png`, o banner passa a ser ele; sem o arquivo, a faixa
    e desenhada com o nome em texto - uso nominativo, que e o que se pode fazer
    sem redistribuir a marca de terceiro.
    """
    arq = banner_arquivo()
    if arq:
        slide.shapes.add_picture(arq, k["Inches"](left), k["Inches"](top),
                                 width=k["Inches"](larg))
        return
    k["card"](slide, left, top, larg, alt, "FFFFFF", "E2E8F0", raio=0.12)
    k["barra"](slide, left, top, 0.055, alt, ORACLE_VERMELHO)
    k["texto"](slide, left + 0.20, top + alt * 0.22, 1.6, alt * 0.6,
               "ORACLE", 15, True, ORACLE_VERMELHO)
    k["texto"](slide, left + 1.28, top + alt * 0.28, larg - 1.5, alt * 0.5,
               texto_dir or "Database  |  Exadata  |  RAC  |  Data Guard  |  "
               "Multitenant", 10, False, "64748B")

# Mascote: rosto em "O" com piscadela, joinha, cilindro de banco e nuvem </>.
# viewBox 0 0 64 58 - o desenho ocupa a caixa toda, sem margem morta.
_SVG = """<svg class="{classe}" viewBox="0 0 64 58" role="img"
 aria-label="ParraDibiei"><title>ParraDibiei</title>
<g stroke="{vermelho}" stroke-width="2.6" stroke-linecap="round">
<path d="M13.5 8.5 11 4M19.5 5.6 18.6 1"/></g>
<g stroke="#0f172a" stroke-width="2.4" stroke-linecap="round">
<path d="M52.5 15.5 55 12.4M55.5 20 59.4 18.6"/></g>
<g fill="none" stroke="{vermelho}" stroke-width="1.9" stroke-linejoin="round">
<path d="M45.6 13.2a4.3 4.3 0 0 1 8.1-1.6 3.5 3.5 0 0 1 4.5 3.4
3.2 3.2 0 0 1-3.2 3.2h-8.8a3.6 3.6 0 0 1-.6-5z"/></g>
<g fill="none" stroke="#0f172a" stroke-width="1.5" stroke-linecap="round"
 stroke-linejoin="round">
<path d="M49.6 12.9 47.9 14.6l1.7 1.7M53.4 12.9l1.7 1.7-1.7 1.7"/></g>
<path d="M10.6 24.4c-1.9-.5-3.1.7-2.6 2.3.3 1.2 1.6 1.8 1.6 1.8-1.2.6-1.2 2.3.2
2.8 1.5.5 3.9.7 6 .2l1.4-7.6c-1.3-.6-2.2-1.6-2.2-3.1 0-1.3.6-2.3.2-3-.5-.9-2-.7
-2.2.6-.2 1.5.1 2.9-.7 4.1-.5.8-1.1 1.4-1.7 1.9z"
 fill="#fff" stroke="#0f172a" stroke-width="1.8" stroke-linejoin="round"/>
<rect x="15" y="11.5" width="34" height="33" rx="13.5" fill="{vermelho}"
 stroke="#0f172a" stroke-width="2"/>
<rect x="21" y="17.5" width="22" height="21" rx="9" fill="#fff"
 stroke="#0f172a" stroke-width="2"/>
<g fill="none" stroke="#0f172a" stroke-width="1.7" stroke-linecap="round">
<path d="M24.4 21.6a3.4 3.4 0 0 1 4.3 0M34.6 21.4a3.4 3.4 0 0 1 4.4.3"/></g>
<ellipse cx="26.6" cy="25.6" rx="2.1" ry="2.7" fill="#0f172a"/>
<path d="M34.4 26.5a3.6 3.6 0 0 1 4.6-.2" fill="none" stroke="#0f172a"
 stroke-width="2" stroke-linecap="round"/>
<path d="M26.4 30.4h11.6a5.8 5.8 0 0 1-11.6 0z" fill="#0f172a"/>
<path d="M29.1 34.3a4.4 4.4 0 0 1 6.2 0 3.2 3.2 0 0 1-6.2 0z"
 fill="{vermelho}"/>
<path d="M46.4 30.6c-1.7-.6-2.6.5-2.2 1.9.3 1.1 1.4 1.6 1.4 1.6-1 .5-1 2 .2 2.5
1.3.5 3.4.6 5.2.2l1.2-6.8" fill="#fff" stroke="#0f172a" stroke-width="1.8"
 stroke-linejoin="round"/>
<g transform="translate(46.5 22)">
<path d="M0 2.6v9.2c0 1.4 2.9 2.6 6.4 2.6s6.4-1.2 6.4-2.6V2.6" fill="{vermelho}"
 stroke="#0f172a" stroke-width="1.7" stroke-linejoin="round"/>
<path d="M0 7.2c0 1.4 2.9 2.6 6.4 2.6s6.4-1.2 6.4-2.6" stroke="#0f172a"
 stroke-width="1.7" fill="none"/>
<ellipse cx="6.4" cy="2.6" rx="6.4" ry="2.6" fill="{vermelho}"
 stroke="#0f172a" stroke-width="1.7"/></g>
<g fill="{vermelho}">
<path d="M6 39.5l.9 2.2 2.2.9-2.2.9-.9 2.2-.9-2.2-2.2-.9 2.2-.9z"/>
<path d="M11.4 46.6l.6 1.5 1.5.6-1.5.6-.6 1.5-.6-1.5-1.5-.6 1.5-.6z"/></g>
<g stroke="#0f172a" stroke-width="2.1" stroke-linecap="round">
<path d="M20 53.5h4.5M44 53.5h4.5"/></g>
<path d="M29.4 50.6l3.2 2.9-3.2 2.9" fill="none" stroke="{vermelho}"
 stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
<path d="M35.4 56.2h4" stroke="{vermelho}" stroke-width="2.5"
 stroke-linecap="round"/>
</svg>"""

TAGLINE = "Dados &middot; Nuvem &middot; Performance &middot; Resultados"


def svg(classe: str = "mascote", vermelho: str = "var(--brand)") -> str:
    """Mascote como SVG inline.

    `vermelho` existe para o caso de a superficie nao definir `--brand` (um
    HTML solto, um e-mail): ali passa-se a cor literal, senao o desenho sai
    sem cor nenhuma.
    """
    return _SVG.format(classe=classe, vermelho=vermelho)


def assinatura(classe: str = "mascote", vermelho: str = "var(--brand)") -> str:
    """Mascote + nome, como aparece no cabecalho."""
    return (f'<div class="brand" title="ParraDibiei - Dados, Nuvem, '
            f'Performance, Resultados">{svg(classe, vermelho)}'
            f'<span class="marca"><b>Parra</b><i>Dibiei</i></span></div>')


def rodape(extra: str = "") -> str:
    """Rodape discreto: assinatura pequena e o slogan.

    Fica em cinza e em corpo pequeno de proposito - e marca registrada, nao
    mais um bloco de informacao disputando a atencao de quem esta no meio de
    um incidente.
    """
    fim = f'<span class="pt">&middot;</span>{extra}' if extra else ""
    return (f'<div class="rodape-marca">{svg("mini")}'
            f'<span class="nm"><b>Parra</b><i>Dibiei</i></span>'
            f'<span class="tag">{TAGLINE}</span>{fim}</div>')


# CSS compartilhado pelas telas. Fica aqui junto do desenho para o tamanho do
# mascote e a assinatura nunca sairem de sincronia com o viewBox.
CSS = """
.brand{display:flex;align-items:center;gap:9px;padding-right:16px;
border-right:1px solid var(--line2);flex:none}
.mascote{width:40px;height:36px;flex:none;display:block}
.marca{font-size:18px;line-height:1;letter-spacing:-.4px;white-space:nowrap}
.marca b{font-weight:800;color:var(--txt)}
.marca i{font-weight:800;font-style:normal;color:var(--brand)}
@media(max-width:820px){.marca{display:none}}
/* Rodape: presenca de marca, nao bloco de conteudo. */
.rodape-marca{display:flex;align-items:center;justify-content:center;gap:8px;
flex-wrap:wrap;padding:22px 16px 30px;color:var(--mut);font-size:11px}
.rodape-marca .mini{width:22px;height:20px;flex:none;opacity:.85}
.rodape-marca .nm{font-size:12px;letter-spacing:-.2px}
.rodape-marca .nm b{font-weight:700;color:var(--mut)}
.rodape-marca .nm i{font-weight:700;font-style:normal;color:var(--brand);
opacity:.85}
.rodape-marca .tag{opacity:.75}
.rodape-marca .pt{opacity:.5}
"""
