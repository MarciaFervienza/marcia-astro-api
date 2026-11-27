"""CORPO NO SIGNO ERRADO — o detector morde o caso real e poupa o certo?

Caso real (26/11): Lua em Virgem, e o relatório entregue dizia "a Lua
escorpiana". Nenhum detector conferia signo de corpo contra o mapa.

Dois lados, como todo detector que fecha relatório:
  A. MORDE   — signo errado, dito de forma explícita ou por adjetivo.
  B. POUPA   — TODAS as afirmações certas de dois mapas reais (cada corpo,
               nas duas formas), e as formas que não falam do mapa da pessoa
               (genérica, trânsito, progressão, casa natural).
  C. CHEGA   — o achado sai de `_detectar_tudo` e não é filtrado pelo
               encanamento como flag-only, léxico ou glossário. Detector que
               ninguém escuta é pior que ausente.
"""
import warnings; warnings.filterwarnings("ignore")
import logging; logging.disable(logging.CRITICAL)
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _fixture as fx
import text_verifier as tv

ok = True


def chk(tag, hits, esperado=True):
    global ok
    bom = bool(hits) == esperado
    ok = ok and bom
    if esperado:
        marca = "MORDE " if hits else ">>> FALHOU (não mordeu)"
    else:
        marca = "LIMPO " if not hits else ">>> FALHOU (falso positivo)"
    print(f"  {marca}  {tag}")
    if hits and (esperado or not bom):
        print(f"      → {hits[0]['suggestion'][:100]}")


HELENA = fx.build_chart(fx.HELENA)   # Lua em Gêmeos, Sol em Virgem, Asc Sagitário
LUCCA = fx.build_chart(fx.LUCCA)     # Lua em Leão, Saturno em Escorpião

print("A) MORDE — signo errado\n")
chk("frase real: 'a Lua escorpiana está carregando' (Lua em Gêmeos)",
    tv._detect_sign_claims("Há uma leveza que pode ofuscar o que a Lua escorpiana "
                           "está carregando por baixo.", HELENA))
chk("'Sua Lua está em Escorpião' (Lua em Gêmeos)",
    tv._detect_sign_claims("Sua Lua está em Escorpião, na casa 8.", HELENA))
chk("'Lua em Virgem' (Lua em Gêmeos; Virgem é o signo do Sol)",
    tv._detect_sign_claims("A Lua em Virgem analisa antes de sentir.", HELENA))
chk("'Saturno, retrógrado, em Leão' (Saturno em Escorpião)",
    tv._detect_sign_claims("Saturno, retrógrado, em Leão pesa sobre a casa 4.", LUCCA))
chk("'o Ascendente em Aquário' (Asc em Sagitário)",
    tv._detect_sign_claims("O Ascendente em Aquário é o que o mundo vê primeiro.", HELENA))
chk("'o Sol geminiano' (Sol em Virgem)",
    tv._detect_sign_claims("Esse Sol geminiano que você carrega é curioso.", HELENA))
chk("'Nodo Norte em Áries' (Nodo Norte em Libra)",
    tv._detect_sign_claims("O Nodo Norte em Áries aponta a direção.", LUCCA))
chk("'você tem uma Lua escorpiana' fala DELA — o artigo não isenta",
    tv._detect_sign_claims("Você tem uma Lua escorpiana, que guarda tudo.", HELENA))
h = tv._detect_sign_claims("o que a Lua escorpiana carrega", HELENA)
chk("a sugestão dá o adjetivo CERTO, no mesmo gênero ('geminiana')",
    [x for x in h if "'geminiana'" in x["suggestion"]])

print("\nB) POUPA — todas as afirmações certas de dois mapas reais\n")
_NOME = {"sun": "Sol", "moon": "Lua", "mercury": "Mercúrio", "venus": "Vênus",
         "mars": "Marte", "jupiter": "Júpiter", "saturn": "Saturno",
         "uranus": "Urano", "neptune": "Netuno", "pluto": "Plutão",
         "chiron": "Quíron", "lilith": "Lilith", "north_node": "Nodo Norte",
         "south_node": "Nodo Sul", "ceres": "Ceres", "pallas": "Palas",
         "juno": "Juno", "vesta": "Vesta"}
_RAIZ = tv._RAIZ_DO_SIGNO
for rot, ch in (("Helena", HELENA), ("Lucca", LUCCA)):
    falsos = []
    n = 0
    for key, p in ch["points"].items():
        nome = _NOME.get(key)
        if not nome:
            continue
        sg = p["sign_pt"]
        frases = [f"O seu {nome} em {sg} fala de algo.",
                  f"Seu {nome} está em {sg}, na casa {p.get('house') or 1}.",
                  f"Esse {nome} {_RAIZ[sg]}o carrega muito.",
                  f"Essa {nome} {_RAIZ[sg]}a carrega muito."]
        for f in frases:
            n += 1
            falsos += tv._detect_sign_claims(f, ch)
    for ang, chave in (("Ascendente", "ascendant"), ("Meio do Céu", "midheaven")):
        n += 1
        falsos += tv._detect_sign_claims(f"O {ang} em {ch[chave]['sign_pt']} marca.", ch)
    print(f"  ({n} afirmações certas de {rot})")
    chk(f"nenhuma afirmação certa de {rot} é acusada", falsos, False)

chk("genérica: 'uma Lua em Câncer pode procurar'",
    tv._detect_sign_claims("Uma Lua em Câncer pode procurar uma mãe que adivinhe.",
                           HELENA), False)
chk("genérica: 'um Saturno leonino'",
    tv._detect_sign_claims("Quem tem um Saturno leonino hesita no palco.", HELENA), False)
chk("progressão: 'a Lua progredida em Escorpião'",
    tv._detect_sign_claims("A Lua progredida em Escorpião aprofunda tudo.", HELENA), False)
chk("trânsito: 'Saturno transita em Peixes'",
    tv._detect_sign_claims("Quando Saturno transita em Peixes, algo muda.", HELENA), False)
chk("casa natural: 'a casa 8, território escorpiano por natureza'",
    tv._detect_sign_claims("A Lua na casa 8 opera em território escorpiano por "
                           "natureza.", HELENA), False)
chk("outro corpo mais perto: 'Vênus, que rege Libra, em Libra'",
    tv._detect_sign_claims("Vênus em Libra está em domicílio.", HELENA), False)

print("\nC) CHEGA — o achado sai de _detectar_tudo e fecha o relatório\n")
tudo = tv._detectar_tudo("Há uma leveza que pode ofuscar o que a Lua escorpiana "
                         "está carregando por baixo.", HELENA)
achados = [v for v in tudo if v["kind"] == "fato:signo_errado"]
chk("_detectar_tudo devolve fato:signo_errado", achados)
filtrado = [v for v in achados
            if v["kind"] in tv.KINDS_FLAG_ONLY
            or v["kind"].startswith(("lexico:", "glossario", "neg_subst"))
            or v.get("no_rewrite")]
chk("não é filtrado pelo encanamento (flag-only, léxico, glossário, no_rewrite)",
    filtrado, False)

print()
print("SIGNO: TUDO PROVADO" if ok else ">>> SIGNO: ALGO FALHOU")
sys.exit(0 if ok else 1)
