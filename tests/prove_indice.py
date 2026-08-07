"""O subtítulo do ÍNDICE é a MESMA string que a seção renderiza?

Regra da Márcia (18/07): fonte única. O índice consome exatamente a string
que a seção mostra — não uma reconstrução a partir dos mesmos dados. O modo
de falha, se houver duas fontes, é visível no mesmo PDF: a regra dos 5° move
Júpiter, a seção diz casa 11 e o índice diz casa 10.

Inclui mapa COM corpo movido pela regra dos 5°, que é onde a divergência
apareceria.
"""
import warnings; warnings.filterwarnings("ignore")
import os, sys, io, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _fixture import build_chart, HELENA, LUCCA
import app, pdf_generator as pg

TITULOS = [
    "Abertura", "Sua tríade: Sol, Lua e Ascendente", "Seu mundo emocional",
    "Casa 4: suas raízes e sua casa interna",
    "Sol e Saturno: seu ideal de eu e suas ferramentas",
    "Mercúrio: como você pensa", "Vênus e Marte: como você ama",
    "Júpiter: onde você acredita em si mesmo",
    "Saturno: onde você amadurece com o tempo",
    "Quíron: sua ferida e seu dom", "Urano: onde você não se encaixa",
    "Netuno: onde você se dissolve", "Plutão: onde você precisa de controle",
    "Lilith: onde você deve insistir em ser você",
    "Nodo Sul e Nodo Norte: de onde você vem e para onde vai",
    "Asteroides: Ceres, Vesta, Juno e Palas", "Fio Condutor",
]

ok = True


def _entradas_do_pdf(data):
    """(nivel, texto) de cada entrada, lidas do PDF construído."""
    marcas = []

    class _Espiao(pg._DocComIndice):
        def afterFlowable(self, flowable):
            m = getattr(flowable, "_toc", None)
            if m:
                marcas.append(m)
            super().afterFlowable(flowable)
    return marcas


def checar(caso, nome, movidos):
    global ok
    chart = build_chart(caso)
    if movidos:
        chart["_house_moves"] = movidos
    pontos = chart["points"]
    # o que a SEÇÃO renderiza
    pares_secao = {}
    capturado = []

    class _Espiao(pg._DocComIndice):
        def afterFlowable(self, flowable):
            m = getattr(flowable, "_toc", None)
            if m:
                capturado.append(m)
            super().afterFlowable(flowable)

    orig = pg._DocComIndice
    pg._DocComIndice = _Espiao
    try:
        body = {"name": nome, "datetime": "1992-09-18T09:50:00",
                "latitude": -19.9227318, "longitude": -43.9450948,
                "timezone": "America/Sao_Paulo", "birth_city": "BH"}
        svg, _ = app._generate_chart_svg(body)
        texto = "# Mapa\n\n" + "".join(
            f"\n## {t}\n\nParágrafo.\n" for t in TITULOS)
        asp = [{"planet_a_pt": "Sol", "planet_b_pt": "Júpiter",
                "type_pt": "conjunção", "orb": a["orb"]}
               for a in chart["aspects"]]
        pg.generate_pdf(report_text=texto, client_name=nome,
                        birth_date="18/09/1992", birth_time="09:50",
                        birth_place="BH", latitude=-19.9, longitude=-43.9,
                        chart_image_url=svg, aspects=asp, points=pontos)
    finally:
        pg._DocComIndice = orig

    # entradas de nível 1 = subtítulos que FORAM AO ÍNDICE.
    # multiBuild percorre a história mais de uma vez (é assim que ele
    # descobre os números de página), então o espião vê a sequência
    # repetida. Uma passada basta — e as passadas têm de ser IDÊNTICAS,
    # o que é verificado abaixo.
    todos = [t for n, t in capturado if n == 1]
    n_esperado = len([s for _, s in
                      (pg._split_section_title(t, pontos) for t in TITULOS) if s])
    passadas = [todos[i:i + n_esperado] for i in range(0, len(todos), n_esperado)]
    assert all(p == passadas[0] for p in passadas), \
        "as passadas do multiBuild divergiram entre si"
    subs_indice = passadas[0]
    # subtítulos que a SEÇÃO calcularia
    subs_secao = [s for _, s in
                  (pg._split_section_title(t, pontos) for t in TITULOS) if s]

    igual = subs_indice == subs_secao
    ok = ok and igual
    print(f"  {'OK    ' if igual else '>>> DIVERGIU'}  {nome}: "
          f"{len(subs_indice)} subtítulos, idênticos caractere a caractere")
    if not igual:
        for a, b in zip(subs_indice, subs_secao):
            if a != b:
                print(f"      índice: {a!r}")
                print(f"      seção : {b!r}")
    return pontos, subs_indice


print("SUBTÍTULO DO ÍNDICE == SUBTÍTULO DA SEÇÃO\n")
checar(HELENA, "Helena Penteado", None)
checar(LUCCA, "Lucca Quitete", None)
print("\ncom corpo MOVIDO pela regra dos 5° (onde a divergência apareceria):")
checar(HELENA, "Helena movida",
       [{"planet": "jupiter", "from_house": 10, "to_house": 11, "gap_to_cusp": 0.4}])

print("\nMORDIDA — se o índice reconstruísse por conta própria:")
_pontos = build_chart(HELENA)["points"]
_sub_real = pg._split_section_title("Júpiter: onde você acredita", _pontos)[1]
_falso = _sub_real.replace("Casa 10", "Casa 11") if "Casa 10" in _sub_real \
    else _sub_real.replace("Casa 11", "Casa 10")
mordeu = _falso != _sub_real
ok = ok and mordeu
print(f"  {'MORDE ' if mordeu else '>>> FALHOU'}  "
      f"reconstrução divergente é detectável: {_sub_real!r} != {_falso!r}")

print()
print("ÍNDICE: FONTE ÚNICA PROVADA" if ok else ">>> ALGO FALHOU")
sys.exit(0 if ok else 1)
