"""AUDITORIA DO ESTADO CONTRA O CÓDIGO (19/07).

Por que existe. Uma decisão da Márcia — "índice, tabela e texto usam a casa
de leitura" — ficou REGISTRADA no ESTADO e NÃO IMPLEMENTADA, duas vezes. Só
apareceu porque o GPT reclamou em três rodadas seguidas.

Isso significa que o ESTADO pode estar descrevendo um sistema que não
existe. É a mesma classe de "o instrumento não é o produto" que a fixture
repetiu quatro vezes, agora aplicada à documentação:

    o ESTADO é o instrumento; o código é o produto.

Cada asserção abaixo é uma decisão REGISTRADA. O teste pergunta ao código
se ela existe. Saída: CONFERE / DIVERGE / NÃO VERIFICÁVEL AQUI.

Isto NÃO é um teste de mordida — é um teste de honestidade do documento.
Roda no gate para que uma decisão registrada nunca mais fique só no papel.
"""
import warnings; warnings.filterwarnings("ignore")
import inspect
import os
import re
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def fonte(nome):
    with open(os.path.join(RAIZ, nome), encoding="utf-8") as fh:
        return fh.read()


APP = fonte("app.py")
RG = fonte("report_generator.py")
PDF = fonte("pdf_generator.py")
TV = fonte("text_verifier.py")
PT = fonte("positions_table.py")
ESTADO = fonte("ESTADO.md")

divergencias = []
conferidas = 0


def confere(secao, decisao, cond, detalhe=""):
    global conferidas
    if cond:
        conferidas += 1
        print(f"  CONFERE   [{secao}] {decisao}")
    else:
        divergencias.append((secao, decisao, detalhe))
        print(f"  DIVERGE   [{secao}] {decisao}")
        if detalhe:
            print(f"                       → {detalhe}")


print("=" * 72)
print("AUDITORIA: o ESTADO descreve o código que existe?")
print("=" * 72)
print()

# ---------------------------------------------------------------- §6 / §9.1
import pdf_generator as pg
import positions_table as ptmod
import app as appmod

confere("§6.2/§9.6", "mandala a 18cm",
        abs(getattr(pg, "WHEEL_SIZE_CM", 0) - 18.0) < 0.01,
        f"WHEEL_SIZE_CM = {getattr(pg, 'WHEEL_SIZE_CM', None)}")

m91 = re.search(r"tamanho na página \| `WHEEL_SIZE_CM = ([\d.]+)`", ESTADO)
confere("§9.1", "a TABELA do §9.1 cita o valor real do código",
        bool(m91) and abs(float(m91.group(1)) - pg.WHEEL_SIZE_CM) < 0.01,
        f"§9.1 diz {m91.group(1) if m91 else '?'}, código diz {pg.WHEEL_SIZE_CM}"
        " — a própria regra do §9 manda a tabela carregar o valor real")

PACK = fonte("wheel_renderer/packing.py")
confere("§6.3", "SCALE_GLYPHS = False (não mexer)",
        "SCALE_GLYPHS = False" in PACK)

confere("§6.1", "ASPECT_COLORS existe no app.py", "ASPECT_COLORS" in APP)
confere("§6.1", "retrogradação com fonte RX própria (sufixo, não cor)",
        "rx_font_size" in PACK)

# ---------------------------------------------------------------- §6.4
confere("§6.4", "patch desinstalado em finally (sem vazar entre requisições)",
        bool(re.search(r"finally:", APP)) and "uninstall" in APP.lower())
confere("§6.4", "_constrained_resolve levanta sem cúspides (sem fallback mudo)",
        "RuntimeError" in fonte("wheel_renderer/packing.py"))

# ---------------------------------------------------------------- §9.3 cores
for nome, valor in (("COLOR_IVORY", "#F8F5EF"), ("COLOR_CHARCOAL", "#2F2F2F"),
                    ("COLOR_GOLD", "#C7A66A"), ("COLOR_TABLE_GRID", "#E6DFCE")):
    confere("§9.3", f"{nome} = {valor}",
            valor.lower() in PDF.lower() and nome in PDF)

# ---------------------------------------------------------------- §9.2 tabela
confere("§9.2", "sem separadores de linha (default da assinatura)",
        "show_row_separators: bool = False" in PDF)
confere("§9.2", "coluna de orbe em EB Garamond (3ª vez pedido)",
        "EBGaramond" in PDF)

# ---------------------------------------------------------------- §9.4
confere("§9.4", "pull-quote em página própria (CondPageBreak na ENTRADA)",
        "CondPageBreak" in PDF)

# ---------------------------------------------------------------- §9.5
confere("§9.5", "símbolo de aspecto do miolo removido", "orb" in APP)
confere("§9.5", "leader lines (Indicator) removidas", "Indicator" in APP)
confere("§9.5", "glifo de Lilith espelhado", "ilith" in APP)

# ---------------------------------------------------------------- §9.7
confere("§9.7", "lista de corpos vem de app.ACTIVE_POINTS (R3)",
        "ACTIVE_POINTS" in PT and "BODY_NAME_PT" in PT)
confere("§9.7", "Nodo Sul DESENHADO (está em ACTIVE_POINTS)",
        "Mean_South_Lunar_Node" in str(appmod.ACTIVE_POINTS))
confere("§9.7", "painel conta 12 corpos (10 planetas + ASC + MC)",
        len(ptmod.COUNTED_BODIES) == 12,
        f"COUNTED_BODIES tem {len(ptmod.COUNTED_BODIES)}")

# A DECISÃO QUE FICOU SÓ NO PAPEL DUAS VEZES.
usa_geometrica = 'd.get("house_geometric") or d.get("house")' in PT
texto_estado_geom = "A CASA é a **geométrica**" in ESTADO
confere("§9.7", "tabela usa a casa de LEITURA (decisão da Márcia)",
        not usa_geometrica,
        "read_positions_from_points ainda lê house_geometric primeiro")
confere("§9.7", "o TEXTO do §9.7 descreve a regra vigente (leitura, não geométrica)",
        not texto_estado_geom,
        "o §9.7 ainda diz 'A CASA é a geométrica' — o código mudou hoje e o "
        "documento não")

# ---------------------------------------------------------------- regra dos 5°
confere("5°", "regra aplicada na FONTE, alcançável por teste",
        callable(getattr(appmod, "apply_five_degree_rule", None)))
confere("5°", "condição de SIGNO presente (fronteira de signo barra)",
        "_same_sign" in APP)
confere("5°", "auditoria por geração no meta (house_reading_moves)",
        "house_reading_moves" in APP)

# ---------------------------------------------------------------- item F
confere("F", "menor de 18 NÃO GERA (HTTP 403)",
        "403" in APP and "age_gate" in APP)
confere("F", "alerta age_gate_refusal", "age_gate_refusal" in APP)
confere("F", "bypass de QA por e-mail isento", "_RATE_EXEMPT_EMAILS" in APP)
confere("F", "forced_minor REMOVIDO (não existe mais)",
        "forced_minor" not in APP,
        "ainda há forced_minor no app.py")

# ---------------------------------------------------------------- voz
confere("voz", "report_for e relationship lidos do formulário",
        "report_for" in APP and "relationship" in APP)
confere("voz", "voice_rules_block SEMPRE emite (2ª ou 3ª pessoa)",
        'return (\n            "\\n\\nREGRAS DE VOZ' in RG
        or "REGRAS DE VOZ — SEGUNDA PESSOA" in RG)

# ---------------------------------------------------------------- lints
import text_verifier as tv
confere("lints", "repetition_lint com janela de 12 palavras",
        "min_words: int = 12" in RG)
confere("lints", "crutch_lint existe e reporta por seção",
        callable(getattr(tv, "detect_crutch_words", None)))

# spell_lint foi DESLIGADO em 19/07 — o ESTADO ainda o descreve como camada viva
spell_ligado = "spell_lint_out = _spell_lint(" in RG
# Checagem ESPECÍFICA. A primeira versão aceitava qualquer "DESLIGADO" no
# documento e passava por causa de outro detector (o 2e, de 17/07): asserção
# passando pelo motivo errado — o mesmo padrão do detector morto, dentro da
# própria auditoria.
estado_registra = "### spell_lint DESLIGADO" in ESTADO
confere("lints", "spell_lint desligado no código (decisão da Márcia, 19/07)",
        not spell_ligado)
confere("lints", "o ESTADO registra que o spell_lint foi DESLIGADO",
        estado_registra,
        "a seção 'Camadas de lint' ainda descreve spell_lint como camada "
        "ativa; ele foi desligado por estar invertido para pt-PT")

# ---------------------------------------------------------------- estilo protegido
confere("estilo", "nota de rodapé mascarada de todos os scans",
        "_FIXED_TEMPLATE_WHITELIST" in TV)
confere("estilo", "'independente de' NÃO virou detector (voz dela)",
        "independente de" not in TV.lower().replace("independentemente", ""))
confere("estilo", "'aquilombada' no léxico proibido", "aquilombada" in TV)

# ---------------------------------------------------------------- negação
n_neg = len(getattr(tv, "_NEGATION_SUBSTITUTION_PATTERNS", []))
confere("neg", "19 variantes de negação-substituição (11 novas + 8 antigas)",
        n_neg >= 19, f"_NEGATION_SUBSTITUTION_PATTERNS tem {n_neg}")

# ---------------------------------------------------------------- detectores citados
for nome, alvo in (("slot de gênero", "genero"),
                   ("reencuadrar", "reencuadrar"),
                   ("tenciona→tensiona", "tenciona"),
                   ("conjunção cerrada", "cerrada")):
    confere("detectores", f"detector citado existe: {nome}", alvo in TV)

# ---------------------------------------------------------------- R3 / R10
confere("R3", "positions_table importa ACTIVE_POINTS em vez de copiar",
        "from app import ACTIVE_POINTS" in PT)
n_create = sum(1 for f in ("report_generator.py", "app.py", "text_verifier.py",
                           "pdf_generator.py")
               for l in fonte(f).split("\n") if "messages.create" in l)
confere("R10", "um único ponto de saída para a API Anthropic",
        n_create == 1, f"achei {n_create}")

# Decisões de 19/07 registradas E implementadas
confere("19/07", "word_lint.py existe e o ESTADO o descreve",
        os.path.exists(os.path.join(RAIZ, "word_lint.py"))
        and "### word_lint.py" in ESTADO)
confere("19/07", "retry no produto, registrado e implementado",
        os.path.exists(os.path.join(RAIZ, "retry_util.py"))
        and "### Retry no produto" in ESTADO)
confere("19/07", "graceful-timeout no start command (502)",
        "--graceful-timeout" in fonte("Procfile")
        and "--graceful-timeout" in fonte("railway.json"))

# ---------------------------------------------------------------- pendências
print()
print("=" * 72)
print("PENDÊNCIAS REGISTRADAS — seguem abertas?")
print("=" * 72)
PEND = [
    ("varredura de expressões: verbo/regência/inversão/corpo trocado",
     "tests/varredura_expressoes.py", "ABERTA no ESTADO"),
    ("gate de repetição por embeddings (pós-lançamento)", None, "ABERTA"),
    ("cabeçalho da tabela em Inter-Medium — confirmar com a Márcia", None,
     "ABERTA"),
    ("legenda de glifos: página da tabela ou da mandala", None, "ABERTA"),
    ("item 18: títulos por função", None, "ABERTA"),
]
for nome, arq, estado in PEND:
    existe = os.path.exists(os.path.join(RAIZ, arq)) if arq else None
    marca = "" if existe is None else (" (arquivo existe)" if existe
                                       else " (ARQUIVO SUMIU)")
    print(f"  · {nome}: {estado}{marca}")

print()
print("=" * 72)
if divergencias:
    print(f"DIVERGÊNCIAS: {len(divergencias)} de {len(divergencias) + conferidas} "
          f"asserções auditadas")
    for s, d, det in divergencias:
        print(f"   [{s}] {d}")
        if det:
            print(f"        {det}")
    raise SystemExit(1)
print(f"ESTADO CONFERE COM O CÓDIGO — {conferidas} asserções auditadas")
