"""VOUCHER DE USO ÚNICO — as propriedades que ele TEM de garantir (18/11).

Roda contra SQLite, que é a MESMA lógica que roda em Postgres. A única
divergência é a cláusula de consumo atômico (UPDATE ... RETURNING contra
BEGIN IMMEDIATE), e ela está isolada num ramo — este arquivo confere que
o ramo existe e que os DOIS lados do ramo têm dentes.

A PROVA QUE IMPORTA é a (G): ela demonstra, no mesmo banco e com as
mesmas threads, que a versão ingênua (SELECT para conferir, UPDATE para
marcar) consome o mesmo voucher DUAS vezes, e que `consumir` não. Sem
esse contraste o teste de concorrência passaria por sorte — e teste que
passa pelo motivo errado é pior que teste ausente, que é lição já paga
por este projeto no primeiro teste de concorrência da fila.
"""
import warnings; warnings.filterwarnings("ignore")
import os
import sys
import tempfile
import threading
import time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import vouchers as V

falhas = 0


def chk(rot, cond, det=""):
    global falhas
    if cond:
        print(f"  OK      {rot}")
    else:
        falhas += 1
        print(f"  ERRADO  {rot}" + (f"  → {det}" if det else ""))


def nova():
    d = tempfile.mkdtemp()
    v = V.Vouchers(os.path.join(d, "vouchers.db"))
    v.criar_tabelas()
    return v


print("=" * 68)
print("A) SEM DATABASE_URL NÃO ABRE — falha alta, sem fallback")
print("=" * 68)
_bak = os.environ.pop("DATABASE_URL", None)
try:
    V.Vouchers()
    chk("levanta sem DATABASE_URL", False, "abriu sem banco")
except RuntimeError as e:
    chk("levanta sem DATABASE_URL", True)
    chk("a mensagem explica por que não há fallback",
        "consumido duas vezes" in str(e))
finally:
    if _bak is not None:
        os.environ["DATABASE_URL"] = _bak

print()
print("=" * 68)
print("B) O CÓDIGO É LEGÍVEL E ÚNICO")
print("=" * 68)
v = nova()
c1 = v.emitir(V.ALUNA, origem="ana@exemplo.com")
chk("forma de leitura MF-XXXX-XXXX", len(c1) == 12 and c1.startswith("MF-")
    and c1[7] == "-", c1)
corpo = V.normalizar(c1)[2:]
chk("alfabeto sem 0/O/1/I/L", not set(corpo) & set("01OIL"), corpo)
emitidos = {V.normalizar(v.emitir(V.PRESENTE)) for _ in range(40)}
chk("40 emissões, 40 códigos distintos", len(emitidos) == 40, str(len(emitidos)))
try:
    v.emitir("qualquer")
    chk("tipo desconhecido é recusado alto", False, "emitiu tipo inválido")
except ValueError:
    chk("tipo desconhecido é recusado alto", True)

print()
print("=" * 68)
print("C) ENTRADA TOLERANTE — o código vem digitado de um PDF, no celular")
print("=" * 68)
alvo = v.emitir(V.ALUNA)
plano = V.normalizar(alvo)
chk("minúsculo com hífen casa", V.normalizar(alvo.lower()) == plano)
chk("com espaços casa",
    V.normalizar(f" {alvo[:3]} {alvo[3:7]}  {alvo[8:]} ") == plano)
chk("sem separador nenhum casa", V.normalizar(plano.lower()) == plano)

print()
print("=" * 68)
print("D) UM USO SÓ, E A RECUSA DIZ POR QUÊ")
print("=" * 68)
c = v.emitir(V.PRESENTE, origem="combo:ana@exemplo.com")
ok, motivo = v.consumir(c, email="clara@exemplo.com", nome="Clara")
chk("o primeiro uso passa", ok and motivo == V.OK, motivo)
ok2, motivo2 = v.consumir(c, email="outra@exemplo.com", nome="Outra")
chk("o segundo uso é recusado", not ok2)
chk("e o motivo é 'ja_usado', não um 'não'", motivo2 == V.JA_USADO, motivo2)
reg = v.consultar(c)
chk("guarda QUEM usou", reg["usado_por"] == "clara@exemplo.com")
chk("guarda QUANDO usou", reg["usado_em"] is not None)
chk("o segundo uso não sobrescreveu o primeiro",
    reg["usado_por"] == "clara@exemplo.com")

ok3, motivo3 = v.consumir("MF-ZZZZ-ZZZZ")
chk("código que não existe diz 'inexistente'", not ok3
    and motivo3 == V.INEXISTENTE, motivo3)

vencido = v.emitir(V.ALUNA, dias=1)
ok4, motivo4 = v.consumir(vencido, agora=time.time() + 2 * 86400)
chk("código vencido diz 'expirado'", not ok4 and motivo4 == V.EXPIRADO,
    motivo4)
chk("vencido NÃO fica marcado como usado",
    v.consultar(vencido)["usado_em"] is None)

print()
print("=" * 68)
print("E) DEVOLVER — a falha do sistema não pode custar o código da cliente")
print("=" * 68)
d = v.emitir(V.ALUNA)
v.consumir(d, email="bete@exemplo.com")
chk("devolver libera", v.devolver(d) is True)
chk("e aí o código funciona de novo", v.consumir(d, email="bete@exemplo.com")[0])
v.devolver(d)
chk("devolver duas vezes não explode e não mente",
    v.devolver(d) is False)
chk("devolver limpa quem usou", v.consultar(d)["usado_por"] is None)

print()
print("=" * 68)
print("F) O RAMO DO POSTGRES EXISTE — é ele que roda em produção")
print("=" * 68)
import inspect as _insp
_src = _insp.getsource(V.Vouchers.consumir)
chk("usa UPDATE ... RETURNING no Postgres", "RETURNING" in _src)
chk("usa BEGIN IMMEDIATE no SQLite", "BEGIN IMMEDIATE" in _src)
chk("a condição de corrida está NA cláusula, não num if",
    "usado_em IS NULL" in _src)
chk("a expiração também está na cláusula, não num if",
    "expira_em >" in _src)

print()
print("=" * 68)
print("G) A MORDIDA — seis threads, um código, exatamente um relatório")
print("=" * 68)


def _corrida(fn, quantas=6):
    """Dispara `quantas` threads ao mesmo tempo contra o mesmo código."""
    porta = threading.Barrier(quantas)
    vitorias = []
    trava = threading.Lock()

    def correr(i):
        porta.wait()
        if fn(i):
            with trava:
                vitorias.append(i)

    ts = [threading.Thread(target=correr, args=(i,)) for i in range(quantas)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return len(vitorias)


# G1 — a versão INGÊNUA, que é a que qualquer um escreveria primeiro.
# Existe aqui para provar que a janela é REAL neste banco, com estas
# threads. Se ela não duplicasse, o sucesso de G2 não significaria nada.
vi = nova()
ingenuo = vi.emitir(V.PRESENTE)
_plano = V.normalizar(ingenuo)


def _consumo_ingenuo(_i):
    con = vi.con()
    cur = con.cursor()
    cur.execute("SELECT usado_em FROM vouchers WHERE codigo=?", (_plano,))
    linha = cur.fetchone()
    if linha is None or linha[0] is not None:
        return False
    time.sleep(0.05)          # a janela: qualquer trabalho entre ler e marcar
    cur.execute("UPDATE vouchers SET usado_em=? WHERE codigo=?",
                (time.time(), _plano))
    return True


duplicou = _corrida(_consumo_ingenuo)
chk("a versão ingênua consome o MESMO código mais de uma vez "
    f"(consumiu {duplicou}x) — a janela é real", duplicou > 1, str(duplicou))

# G2 — a versão do módulo, no mesmo banco, com as mesmas threads.
va = nova()
atomico = va.emitir(V.PRESENTE)


def _consumo_atomico(i):
    return va.consumir(atomico, email=f"t{i}@exemplo.com")[0]


venceu = _corrida(_consumo_atomico)
chk(f"`consumir` deixa passar exatamente 1 (passou {venceu})", venceu == 1,
    str(venceu))
chk("e o código fica marcado como usado uma vez só",
    va.consultar(atomico)["usado_em"] is not None)

print()
print("=" * 68)
print("H) A LIGAÇÃO COM A GERAÇÃO — reserva antes, devolve se falhar")
print("=" * 68)
# O banco e a chave entram ANTES do import: o app lê os dois no topo do
# módulo, e a seção I chama o endpoint de verdade pelo cliente de teste.
_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = os.path.join(_tmp, "prova.db")
os.environ.setdefault("API_SECRET_KEY", "chave-de-teste-" + "x" * 20)
import app as _app

_end = _insp.getsource(_app.generate_report_endpoint)
chk("o endpoint tira o código do corpo com pop (não vaza para o chart)",
    'body.pop("voucher"' in _end)
chk("aceita o apelido 'codigo' do formulário", 'body.pop("codigo"' in _end)
chk("consome ANTES de enfileirar — código ruim não ganha vaga na fila",
    _end.index("consumir(") < _end.index("enfileirar("))
chk("consome ANTES de gerar", _end.index("consumir(")
    < _end.index("executar_geracao("))
chk("banco fora do ar NÃO vira 'passou sem conferir'",
    "_vouchers_ou_erro()" in _end and "return _v_err" in _end)
chk("recusa com 403 e com código de erro legível",
    "403" in _end and "voucher_" in _end)
chk("o código viaja no corpo, que é o que a fila persiste",
    'body["_voucher"]' in _end)

_casca = _insp.getsource(_app.executar_geracao)
chk("executar_geracao virou casca sobre o núcleo",
    "_nucleo_da_geracao(" in _casca)
chk("a casca tira o código do corpo", '.pop("_voucher"' in _casca)
chk("devolve quando o núcleo levanta", "except BaseException" in _casca
    and _casca.count("_devolver_voucher(") >= 2)
chk("devolve quando o núcleo recusa (http != 200)", "http != 200" in _casca)
chk("o worker também passa pela casca",
    "executar_geracao(" in _insp.getsource(_app.executar_geracao_para_fila))

_dev = _insp.getsource(_app._devolver_voucher)
chk("devolver nunca levanta — é caminho de falha", "except Exception" in _dev)

chk("as três recusas têm mensagem para a CLIENTE",
    all(m in _app._RECUSA_VOUCHER for m in ("inexistente", "ja_usado",
                                            "expirado", "ausente")))
chk("o interruptor nasce DESLIGADO — o campo do Wix vem antes da exigência",
    _app.VOUCHER_OBRIGATORIO is False)

print()
print("=" * 68)
print("I) /emitir-acesso — o código nasce e o e-mail sai do mesmo lugar")
print("=" * 68)
_cli = _app.app.test_client()
_CH = {"X-API-Key": os.environ["API_SECRET_KEY"]}


def _emitir(**kw):
    corpo = {"enviar": False}
    corpo.update(kw)
    return _cli.post("/emitir-acesso", json=corpo, headers=_CH)


_r = _cli.post("/emitir-acesso", json={"email": "a@b.com",
                                       "produto": "leitura"})
chk("sem chave de API, 401", _r.status_code == 401, str(_r.status_code))

_r = _emitir(email="semarroba", produto="leitura")
chk("e-mail inválido é recusado", _r.status_code == 400)
_r = _emitir(email="a@b.com", produto="curso-que-nao-existe")
chk("produto desconhecido é recusado, dizendo quais valem",
    _r.status_code == 400 and "leitura" in _r.get_json()["message"])

_r = _emitir(email="Ana@Exemplo.com", nome="Ana Souza", produto="leitura")
_j = _r.get_json()
chk("curso avulso emite UM código", _r.status_code == 200
    and _j["codigo_aluna"] and _j["codigo_presente"] is None)
chk("o e-mail é normalizado para minúsculo", _j["email"] == "ana@exemplo.com")
_codigo_ana = _j["codigo_aluna"]

_r2 = _emitir(email="ana@exemplo.com", nome="Ana Souza", produto="leitura")
chk("chamar duas vezes NÃO emite um código órfão",
    _r2.get_json()["codigo_aluna"] == _codigo_ana
    and _r2.get_json()["reaproveitado"] is True)

_r3 = _emitir(email="bete@exemplo.com", nome="Bete Prado", produto="combo")
_j3 = _r3.get_json()
chk("combo emite DOIS códigos, e diferentes",
    _j3["codigo_aluna"] and _j3["codigo_presente"]
    and _j3["codigo_aluna"] != _j3["codigo_presente"])

_v2 = V.abrir()
chk("o código do combo é do tipo 'presente'",
    _v2.consultar(_j3["codigo_presente"])["tipo"] == V.PRESENTE)
chk("e guarda de qual compra veio",
    _v2.consultar(_j3["codigo_presente"])["origem"] == "combo:bete@exemplo.com")

_txt = _j3["texto"]
chk("o texto volta junto, para a Marcelle copiar se o envio falhar",
    bool(_txt))
chk("sem travessão no texto da cliente", "—" not in _txt)
chk("promete 24 horas", "24 horas" in _txt)
chk("NÃO promete 1 hora", "1 hora" not in _txt)
chk("traz os dois códigos", _j3["codigo_aluna"] in _txt
    and _j3["codigo_presente"] in _txt)
chk("traz o endereço do formulário", _app.FORM_URL in _txt)
chk("o bloco de presente só existe no combo",
    "presentear" in _txt and "presentear" not in _j["texto"])
chk("chama a pessoa pelo primeiro nome", _txt.startswith("Oi, Bete!"))

# 23/11: o primeiro e-mail real chegou com as linhas grudadas e a URL
# do formulário à vista. O HTML existe para as duas coisas.
_html = _app.html_de_texto(_txt)
chk("o HTML separa os parágrafos", _html.count("<p>") >= 8,
    str(_html.count("<p>")))
chk("o link vira texto clicável, com o endereço no href",
    '<a href="%s">Clique aqui para preencher o formulário</a>'
    % _app.FORM_URL in _html)
chk("a URL do formulário NÃO aparece como texto solto",
    _html.replace('href="%s"' % _app.FORM_URL, "").count(_app.FORM_URL) == 0)
chk("o código vai em negrito",
    "<strong>%s</strong>" % _j3["codigo_aluna"] in _html)
chk("texto com < e & é escapado, não vira marcação",
    "&lt;b&gt;" in _app.html_de_texto("a <b> & c"))

_r4 = _emitir(email="cida@exemplo.com", produto="preditivas",
              acesso="Acesso: entre em area-de-alunos com seu e-mail.")
chk("o bloco de acesso ao curso entra quando é passado",
    "area-de-alunos" in _r4.get_json()["texto"])

chk("sem SENHA_PAGINA posta, o e-mail não fala de senha nenhuma",
    "Senha da página" not in _txt)
_app.SENHA_PAGINA = "senha-de-teste"
chk("com SENHA_PAGINA posta, a senha entra",
    "Senha da página: senha-de-teste"
    in _app.texto_boas_vindas("leitura", codigo_aluna="MF-0000-0000"))
_app.SENHA_PAGINA = ""

print()
print("=" * 68)
print("J) A RECUSA NÃO É SILENCIOSA — a cliente é avisada e o painel vê")
print("=" * 68)
# Em 23/11 a Márcia usou o mesmo código duas vezes. A segunda foi
# recusada, a página de obrigado disse "está sendo escrito" igual, e a
# recusa só pôde ser inferida pela AUSÊNCIA de uma geração no painel.
_enviados = []
_htmls = []
_app._enviar_email_bruto = (
    lambda assunto, texto, destino=None, detalhe_out=None, html=None:
    (_enviados.append((assunto, texto, destino)) or _htmls.append(html)
     or True))
_app._missing_required_keys = lambda: []
_app.chaves_malformadas = lambda: []


def _gerar(email, voucher):
    return _cli.post("/generate-report", headers=_CH, json={
        "name": "Dora Lima", "email": email, "gender": "feminino",
        "birth_date": "1990-05-08", "birth_time": "14:20",
        "birth_city": "Fortaleza, CE, Brasil", "voucher": voucher})


_usado = _v2.emitir(V.ALUNA, origem="leitura:dora@exemplo.com")
_v2.consumir(_usado, email="dora@exemplo.com")
_antes = len(_app._ULTIMAS_GERACOES)
_rj = _gerar("dora@exemplo.com", _usado)
chk("código já usado devolve 403", _rj.status_code == 403,
    str(_rj.status_code))
chk("e a cliente recebe um e-mail", len(_enviados) == 1
    and _enviados[0][2] == "dora@exemplo.com", str(len(_enviados)))
_assunto, _corpo, _dest = _enviados[0] if _enviados else ("", "", "")
chk("o e-mail diz o MOTIVO (já foi usado)", "já foi usado" in _corpo)
chk("diz que o relatório não foi gerado",
    "não conseguimos gerar o seu Mapa Natal" in _corpo)
chk("manda para a Marcelle, no executivo@",
    "Marcelle" in _corpo and "executivo@marciafervienza.com" in _corpo)
chk("pede 'por favor, entre em contato'",
    "por favor, entre em contato com a Marcelle" in _corpo)
# 23/11: o aviso chegou à Márcia em texto corrido, no mesmo dia em que o
# e-mail de boas-vindas ganhou cara. Os dois vão para a mesma pessoa.
chk("o aviso vai na MESMA moldura do e-mail de boas-vindas",
    bool(_htmls and _htmls[0]) and "Astrologia e Astroterapia" in _htmls[0]
    and _htmls[0].count("<p ") >= 4 and "já foi usado" in _htmls[0])
chk("no HTML, o executivo@ é um link",
    'href="mailto:executivo@marciafervienza.com"' in (_htmls[0] or ""))
chk("quem assina é a EQUIPE, no texto e no HTML",
    _corpo.rstrip().endswith("Um abraço\nEquipe da Márcia Fervienza")
    and "Equipe da Márcia Fervienza" in _htmls[0]
    and "Atenciosamente" not in _corpo)
chk("chama pelo primeiro nome", _corpo.startswith("Oi, Dora!"))
chk("sem travessão", "—" not in _corpo and "—" not in _assunto)
_novos = list(_app._ULTIMAS_GERACOES)[_antes:]
chk("a recusa aparece no painel, com o motivo no desfecho",
    any(g.get("desfecho") == "voucher_ja_usado" for g in _novos),
    str([g.get("desfecho") for g in _novos]))

_rj2 = _gerar("dora@exemplo.com", "MF-ZZZZ-ZZZZ")
chk("código inexistente também avisa", _rj2.status_code == 403
    and len(_enviados) == 2 and "Não encontramos esse código" in _enviados[1][1])

# Três recusas seguidas: sem o estorno, a terceira seria 429 (o limite
# por e-mail é 2 em 24h) e a cliente ficaria travada tendo gerado zero.
_rj3 = _gerar("dora@exemplo.com", "MF-YYYY-YYYY")
chk("errar o código NÃO gasta o limite de taxa da cliente",
    _rj3.status_code == 403, f"devolveu {_rj3.status_code}")

_aviso = _insp.getsource(_app._avisar_recusa_voucher)
chk("o aviso nunca levanta — é caminho de recusa",
    "except Exception" in _aviso)

print()
print("=" * 68)
print("K) CÓDIGO DE ADMIN, LINK CURTO E ONDE DIGITAR O CÓDIGO (23/11)")
print("=" * 68)
_geradas = []
_app.executar_geracao = (
    lambda body, ctx=None: (_geradas.append(dict(body)) or
                            ({"status": "ok"}, 200)))
_app.FILA_ASSINCRONA = False
# As seções anteriores gastaram o limite por IP (4 em 24h) com recusas.
# Zera aqui para que esta seção meça o código de admin, não o limite.
_app._rate_ip_hits.clear()
_app._rate_email_hits.clear()

_app.CODIGO_ADMIN = ""
_rk = _gerar("nina@exemplo.com", "ADMIN-TESTE-2026-XYZ")
chk("sem CODIGO_ADMIN posto, não existe atalho", _rk.status_code == 403,
    str(_rk.status_code))

_app.CODIGO_ADMIN = "curto"
_rk = _gerar("nina@exemplo.com", "curto")
chk("código de admin com menos de 12 caracteres é IGNORADO",
    _rk.status_code == 403, str(_rk.status_code))

_app.CODIGO_ADMIN = "ADMIN-TESTE-2026-XYZ"
_r1 = _gerar("nina@exemplo.com", "admin teste 2026 xyz")
_r2 = _gerar("nina@exemplo.com", "ADMIN-TESTE-2026-XYZ")
chk("o código de admin passa", _r1.status_code == 200, str(_r1.status_code))
chk("e passa de novo: não é gasto", _r2.status_code == 200,
    str(_r2.status_code))
chk("não vira voucher a devolver (não entra no corpo)",
    all("_voucher" not in g for g in _geradas[-2:]))
_app._rate_email_hits.clear()
_app._rate_ip_hits.clear()
chk("um código quase igual NÃO passa",
    _gerar("nina@exemplo.com","ADMIN-TESTE-2026-XYW").status_code == 403)
_app.CODIGO_ADMIN = ""

_bloco = _app._bloco_formulario("MF-2345-6789")
chk("o e-mail diz EM QUE CAMPO o código entra",
    'no campo "Código de acesso"' in _bloco)
# A função está trocada por um dublê desde a seção J; lê do arquivo.
_fonte_app = open(_app.__file__, encoding="utf-8").read()
_i_env = _fonte_app.index("def _enviar_email_bruto(")
_envio = _fonte_app[_i_env:_fonte_app.index("\ndef ", _i_env + 10)]
chk("clique rastreado no HTML (atrás do texto), nunca no texto puro",
    '"click_tracking"' in _envio and '"enable": True' in _envio
    and '"enable_text": False' in _envio)
_h = _app.html_de_texto(_bloco)
chk("no HTML o endereço fica ATRÁS do texto, não à vista",
    "Clique aqui para preencher o formulário</a>" in _h
    and ">" + _app.FORM_URL not in _h, _h[:200])

print()
print("=" * 68)
print("L) O E-MAIL DE BOAS-VINDAS TEM A CARA DA MÁRCIA (23/11)")
print("=" * 68)
import html as _hm
import re as _re2
_tx = _app.texto_boas_vindas("combo", nome="MARIA Souza",
                             codigo_aluna="MF-2345-6789",
                             codigo_presente="MF-ABCD-EFGH")
_hx = _app.html_boas_vindas("combo", nome="MARIA Souza",
                            codigo_aluna="MF-2345-6789",
                            codigo_presente="MF-ABCD-EFGH")
_visivel = _hm.unescape(_re2.sub(r"<[^>]+>", " ", _hx))
_visivel = _re2.sub(r"\s+", " ", _visivel)
chk("nome em maiúsculas vira nome de carta", "Oi, Maria!" in _tx
    and "Oi, Maria!" in _visivel and "MARIA" not in _visivel)
chk("nome com maiúscula no meio passa como veio",
    _app._primeiro_nome("DeAndra Silva") == "DeAndra")
# As duas versões saem das mesmas frases. Se alguém escrever uma frase
# direto numa das funções, ela falta na outra e esta asserção cai.
_frases = [l.strip() for l in _tx.split("\n") if l.strip()
           and not l.startswith(("Link:", "- - -", "Código de acesso:"))]
_faltam = [f for f in _frases
           if _re2.sub(r"\s+", " ", f) not in _visivel]
chk("toda frase do texto está no HTML", not _faltam, str(_faltam[:3]))
chk("os dois códigos aparecem, cada um uma vez",
    _visivel.count("MF-2345-6789") == 1
    and _visivel.count("MF-ABCD-EFGH") == 1)
chk("o endereço do formulário fica atrás do botão, não à vista",
    _app.FORM_URL not in _visivel and _hx.count('href="') == 2)
chk("sem travessão no HTML", "—" not in _hx)
chk("curso avulso não leva o cartão de presente",
    "presente" not in _app.html_boas_vindas(
        "leitura", nome="Ana", codigo_aluna="MF-2345-6789").lower())
chk("o endpoint manda ESTA versão, não o texto com parágrafos",
    "html=html_boas_vindas(" in _insp.getsource(_app._emitir_acesso_nucleo))

print()
print("=" * 68)
print("M) COMPRA AVULSA DO MAPA NATAL NA LOJA TAMBÉM RECEBE CÓDIGO (23/11)")
print("=" * 68)
# O e-mail automático da loja não levava código. Com a exigência ligada,
# quem pagou só pelo relatório seria justamente quem ficaria de fora.
_tm = _app.texto_boas_vindas("mapa", nome="Rita", codigo_aluna="MF-2345-6789")
_hmapa = _app.html_boas_vindas("mapa", nome="Rita",
                               codigo_aluna="MF-2345-6789")
chk("fala em COMPRA, não em inscrição",
    "Sua compra do Mapa Natal está confirmada." in _tm
    and "inscrição" not in _tm and "inscrição" not in _hmapa)
chk("não chama de brinde o que a pessoa pagou",
    "brinde" not in _tm and "brinde" not in _hmapa)
chk("leva o código e o prazo de 24 horas",
    "MF-2345-6789" in _tm and "em até 24 horas" in _tm
    and "MF-2345-6789" in _hmapa)
chk("assunto próprio, sem travessão",
    _app.assunto_boas_vindas("mapa") == "Seu Mapa Natal: próximo passo")
chk("o assunto dos cursos não mudou",
    _app.assunto_boas_vindas("leitura")
    == "Bem-vinda ao Curso de Leitura de Mapa Natal")
_m1 = _emitir(email="rita@exemplo.com", nome="Rita", produto="mapa",
              pedido="10001").get_json()
_m1b = _emitir(email="rita@exemplo.com", nome="Rita", produto="mapa",
               pedido="10001").get_json()
_m2 = _emitir(email="rita@exemplo.com", nome="Rita", produto="mapa",
              pedido="10002").get_json()
chk("o mesmo pedido repetido reaproveita o código (clique duplo do Wix)",
    _m1["codigo_aluna"] == _m1b["codigo_aluna"])
chk("DOIS pedidos do mesmo e-mail são DOIS códigos",
    _m1["codigo_aluna"] != _m2["codigo_aluna"],
    f'{_m1["codigo_aluna"]} / {_m2["codigo_aluna"]}')
chk("compra avulsa não ganha voucher de presente",
    not _m1.get("codigo_presente"))

# Duas ou mais unidades no mesmo pedido: um código por relatório.
_q = _emitir(email="rita@exemplo.com", nome="Rita", produto="mapa",
             pedido="10003", quantidade="3").get_json()
_todos = [_q["codigo_aluna"]] + _q["codigos_extras"]
chk("3 unidades são 3 códigos diferentes", len(set(_todos)) == 3,
    str(_todos))
chk("os 3 estão no e-mail, com a frase que diz quantos são",
    all(c in _q["texto"] for c in _todos)
    and "Você comprou 3 relatórios." in _q["texto"])
_qb = _emitir(email="rita@exemplo.com", nome="Rita", produto="mapa",
              pedido="10003", quantidade=3.0).get_json()
chk("repetir o pedido devolve os MESMOS 3 (não vira 6)",
    [_qb["codigo_aluna"]] + _qb["codigos_extras"] == _todos)
_qh = _app.html_boas_vindas("mapa", nome="Rita", codigo_aluna=_todos[0],
                            codigos_extras=_todos[1:])
chk("os 3 estão no HTML, cada um no seu cartão",
    all(_qh.count(c) == 1 for c in _todos))
_q2 = _emitir(email="rita@exemplo.com", produto="mapa", pedido="10004",
              quantidade=2).get_json()
chk("com 2, a frase fala em 'o outro código'",
    "O outro código é este:" in _q2["texto"])
_qx = _emitir(email="rita@exemplo.com", produto="mapa", pedido="10005",
              quantidade=5000).get_json()
chk("quantidade absurda bate no teto, não vira 5000 códigos",
    len(_qx["codigos_extras"]) == _app._MAX_RELATORIOS_POR_PEDIDO - 1)
_ql = _emitir(email="rita@exemplo.com", produto="mapa", pedido="10006",
              quantidade="abc").get_json()
chk("quantidade ilegível vira 1, não erro",
    _ql["status"] == "ok" and _ql["codigos_extras"] == [])
_qc = _emitir(email="curso@exemplo.com", produto="leitura",
              quantidade=3).get_json()
chk("curso ignora quantidade: é um por pessoa", _qc["codigos_extras"] == [])

# Quem comprou 3 com o mesmo e-mail tem de conseguir gerar os 3. O
# limite de taxa é 2 por e-mail em 24h e é contado antes do código.
_app._rate_ip_hits.clear()
_app._rate_email_hits.clear()
_st = [_gerar("rita@exemplo.com", c).status_code for c in _todos]
chk("os 3 códigos geram 3 relatórios no mesmo e-mail", _st == [200] * 3,
    str(_st))
_app._rate_ip_hits.clear()
chk("e o mesmo código não gera um quarto",
    _gerar("rita@exemplo.com", _todos[0]).status_code == 403)

print()
print("=" * 68)
print("N) PÁGINA DA EQUIPE — emitir sem terminal (23/11)")
print("=" * 68)
# A Marcelle não roda programa de terminal. A página é a ferramenta dela.
_app.CODIGO_ADMIN = "EQUIPE-TESTE-2026-ABC"
_app._EQUIPE_FALHAS.clear()
_g = _cli.get("/emitir")
_pg = _g.get_data(as_text=True)
chk("a página abre no navegador, sem chave de API", _g.status_code == 200
    and "<form" in _pg and 'name="email"' in _pg and 'name="produto"' in _pg)
chk("pede o código da equipe num campo de senha",
    'type="password"' in _pg and 'name="codigo"' in _pg)
chk("oferece os quatro produtos",
    all(f'value="{k}"' in _pg for k in ("leitura", "preditivas", "combo",
                                         "mapa")))
chk("não se oferece a buscadores", "noindex" in _pg)
chk("a chave da API não aparece na página",
    os.environ["API_SECRET_KEY"] not in _pg)

_n_antes = len(_enviados)
_dados = {"email": "ines@exemplo.com", "nome": "INES Prado",
          "produto": "leitura"}
_pe = _cli.post("/emitir", data=dict(_dados, codigo="codigo-errado-000"))
chk("código errado: 403 e NADA é enviado", _pe.status_code == 403
    and len(_enviados) == _n_antes, f"{_pe.status_code}")
_pet = _pe.get_data(as_text=True)
chk("o erro devolve o que foi digitado, menos o código",
    "ines@exemplo.com" in _pet and "codigo-errado-000" not in _pet)

_po = _cli.post("/emitir", data=dict(_dados,
                                     codigo="equipe teste 2026 abc"))
_pot = _po.get_data(as_text=True)
chk("código certo: 200 e o e-mail sai", _po.status_code == 200
    and len(_enviados) == _n_antes + 1
    and _enviados[-1][2] == "ines@exemplo.com", f"{_po.status_code}")
import re as _re3
_mc = _re3.search(r"MF-[0-9A-Z]{4}-[0-9A-Z]{4}", _pot)
chk("a página mostra o código emitido", bool(_mc)
    and _mc.group(0) in _enviados[-1][1])
chk("é o MESMO e-mail do caminho automático",
    "Sua inscrição no Curso de Leitura de Mapa Natal está confirmada."
    in _enviados[-1][1] and "Oi, Ines!" in _enviados[-1][1])
_po2 = _cli.post("/emitir", data=dict(_dados,
                                      codigo="EQUIPE-TESTE-2026-ABC"))
chk("enviar de novo reaproveita o código e avisa",
    _mc and _mc.group(0) in _po2.get_data(as_text=True)
    and "já existia" in _po2.get_data(as_text=True))

_pc = _cli.post("/emitir", data={"email": "zoe@exemplo.com", "nome": "Zoe",
                                 "produto": "combo",
                                 "codigo": "EQUIPE-TESTE-2026-ABC"})
chk("combo pela página: dois códigos",
    len(set(_re3.findall(r"MF-[0-9A-Z]{4}-[0-9A-Z]{4}",
                         _pc.get_data(as_text=True)))) == 2)

_app.CODIGO_ADMIN = ""
_ps = _cli.post("/emitir", data=dict(_dados, codigo=""))
chk("sem CODIGO_ADMIN posto, a página não emite para ninguém",
    _ps.status_code == 403)
_app.CODIGO_ADMIN = "EQUIPE-TESTE-2026-ABC"
_app._EQUIPE_FALHAS.clear()
for _ in range(_app._EQUIPE_MAX_FALHAS):
    _cli.post("/emitir", data=dict(_dados, codigo="chute"))
_pb = _cli.post("/emitir", data=dict(_dados,
                                     codigo="EQUIPE-TESTE-2026-ABC"))
chk("depois de 8 erros, o IP espera: nem o código certo passa",
    _pb.status_code == 429, f"{_pb.status_code}")
_app._EQUIPE_FALHAS.clear()
_app.CODIGO_ADMIN = ""

print()
print("=" * 68)
print("O) PAGBANK — a venda de curso emite o acesso sozinha (23/11)")
print("=" * 68)
chk("descrição do item decide o curso, sem acento e sem caixa",
    _app._pagbank_produto(["Curso de Leitura de Mapa Natal 2027"]) == "leitura"
    and _app._pagbank_produto(["CURSO DE TÉCNICAS DE PREVISÃO"]) == "preditivas"
    and _app._pagbank_produto(["Astrologia Preditiva"]) == "preditivas"
    and _app._pagbank_produto(["Combo Leitura + Preditivas"]) == "combo"
    and _app._pagbank_produto(["Curso de Leitura", "Curso Preditivas"])
    == "combo")
chk("o que não é curso NÃO vira curso (consulta, live)",
    _app._pagbank_produto(["Consulta de Astroterapia"]) is None
    and _app._pagbank_produto(["Live de Eclipses"]) is None
    and _app._pagbank_produto([]) is None)


class _RespPB:
    def __init__(self, status, corpo):
        self.status_code, self.content = status, corpo.encode("utf-8")


def _xml_pb(codigo, status, desc, email="bia@exemplo.com", nome="BIA Reis"):
    return ("<transaction><code>%s</code><status>%s</status>"
            "<grossAmount>449.99</grossAmount><sender><name>%s</name>"
            "<email>%s</email></sender><items><item><id>1</id>"
            "<description>%s</description><quantity>1</quantity>"
            "<amount>449.99</amount></item></items></transaction>"
            % (codigo, status, nome, email, desc))


_pb_resposta = {}
_pb_chamadas = []


def _pb_get(url, params=None, timeout=None):
    _pb_chamadas.append((url, dict(params or {})))
    return _pb_resposta["r"]


_app.requests.get = _pb_get
_NC = "A1B2C3-D4E5F6A1B2C3-D4E5F6A1B2C3-D4E5F6"


def _avisa_pb(cod=_NC):
    return _cli.post("/pagbank-notificacao", data={
        "notificationCode": cod, "notificationType": "transaction"})


_app.PAGBANK_EMAIL, _app.PAGBANK_TOKEN = "", ""
_pb_resposta["r"] = _RespPB(200, _xml_pb("TX-0001", "3", "Curso de Leitura"))
_r0 = _avisa_pb()
chk("sem credenciais, 503 (o PagBank tenta de novo) e nada é consultado",
    _r0.status_code == 503 and not _pb_chamadas, str(_r0.status_code))

_app.PAGBANK_EMAIL, _app.PAGBANK_TOKEN = "conta@teste.com", "token-de-teste"
_n0 = len(_enviados)
_r1 = _avisa_pb()
chk("venda PAGA de curso: 200 e o e-mail de boas-vindas sai",
    _r1.status_code == 200 and len(_enviados) == _n0 + 1
    and _enviados[-1][2] == "bia@exemplo.com", str(_r1.status_code))
chk("é o e-mail do curso certo, com o nome arrumado",
    "Curso de Leitura de Mapa Natal está confirmada" in _enviados[-1][1]
    and "Oi, Bia!" in _enviados[-1][1])
chk("o servidor PERGUNTOU ao PagBank com o código do aviso",
    _pb_chamadas and _pb_chamadas[-1][0].endswith(_NC)
    and _pb_chamadas[-1][1].get("token") == "token-de-teste")
_cod_pb = _re3.search(r"MF-[0-9A-Z]{4}-[0-9A-Z]{4}", _enviados[-1][1]).group(0)

# O PagBank avisa em "paga" (3) e de novo em "disponível" (4), dias
# depois. Se a aluna já usou o código, o 2º aviso não pode virar código
# novo: uma compra, uma entrega.
_v2.consumir(_cod_pb, email="bia@exemplo.com")
_pb_resposta["r"] = _RespPB(200, _xml_pb("TX-0001", "4", "Curso de Leitura"))
_n1 = len(_enviados)
_r2 = _avisa_pb()
chk("2º aviso da MESMA venda, código já usado: nada novo é emitido",
    _r2.status_code == 200 and len(_enviados) == _n1
    and _r2.get_json().get("ja_entregue") is True, str(_r2.get_json()))

_pb_resposta["r"] = _RespPB(200, _xml_pb("TX-0002", "1", "Curso de Leitura",
                                         email="caio@exemplo.com"))
_n2 = len(_enviados)
chk("aguardando pagamento NÃO libera nada",
    _avisa_pb().get_json().get("motivo") == "nao_pago"
    and len(_enviados) == _n2)

_pb_resposta["r"] = _RespPB(200, _xml_pb("TX-0003", "3",
                                         "Consulta de Astroterapia",
                                         email="dani@exemplo.com"))
chk("consulta paga não recebe boas-vindas de curso",
    _avisa_pb().get_json().get("motivo") == "nao_e_curso"
    and len(_enviados) == _n2)

_pb_resposta["r"] = _RespPB(200, _xml_pb("TX-0004", "3", "Combo dos cursos",
                                         email="eva@exemplo.com"))
_avisa_pb()
chk("combo pelo PagBank: dois códigos no mesmo e-mail",
    len(set(_re3.findall(r"MF-[0-9A-Z]{4}-[0-9A-Z]{4}",
                         _enviados[-1][1]))) == 2)

_pb_resposta["r"] = _RespPB(200, _xml_pb("TX-0005", "3", "Curso de Leitura",
                                         email=""))
_n3 = len(_enviados)
_r5 = _avisa_pb()
chk("curso pago SEM e-mail: a equipe é alertada, com o link da página",
    _r5.get_json().get("motivo") == "sem_email"
    and len(_enviados) == _n3 + 1 and "/emitir" in _enviados[-1][1]
    and "TX-0005" in _enviados[-1][1])

_pb_resposta["r"] = _RespPB(401, "Unauthorized")
chk("PagBank recusando a consulta: 502, para ele tentar de novo",
    _avisa_pb().status_code == 502)
_nq = len(_pb_chamadas)
_rf = _cli.post("/pagbank-notificacao", data={
    "notificationCode": "curto", "notificationType": "transaction"})
chk("aviso malformado não gera consulta nem e-mail",
    _rf.status_code == 200 and len(_pb_chamadas) == _nq)
# --- formato novo: JSON por evento (ORDER.CHARGE) --------------------------
# A tela de Integrações da conta só oferece este cadastro. O aviso traz o
# pedido inteiro, mas o servidor só confia no `id` e pergunta o resto.


class _RespJSON:
    def __init__(self, status, obj):
        self.status_code, self._o = status, obj

    def json(self):
        return self._o


def _pedido_pb(oid, status, item, email="gil@exemplo.com", nome="GIL Sá"):
    return {"id": oid, "reference_id": "ref-1",
            "customer": {"name": nome, "email": email},
            "items": [{"name": item, "quantity": 1, "unit_amount": 44999}],
            "charges": [{"id": "CHAR_1", "status": status,
                         "amount": {"value": 44999, "currency": "BRL"}}]}


_pb_json_chamadas = []


def _pb_get2(url, params=None, headers=None, timeout=None):
    _pb_json_chamadas.append((url, dict(headers or {})))
    return _pb_resposta["r"]


_app.requests.get = _pb_get2
_app.PAGBANK_EMAIL, _app.PAGBANK_TOKEN = "", "token-de-teste"
_OID = "ORDE_AAAA1111-BBBB-2222-CCCC-333344445555"
_pb_resposta["r"] = _RespJSON(200, _pedido_pb(_OID, "PAID",
                                              "Curso de Leitura de Mapa"))
_nj = len(_enviados)
# O aviso MENTE de propósito: diz outro e-mail e outro curso. O que vale
# é o que o PagBank responde na consulta.
_rj1 = _cli.post("/pagbank-notificacao", json=_pedido_pb(
    _OID, "PAID", "Curso Preditivas", email="intruso@exemplo.com"))
chk("JSON de pedido pago: 200 e o e-mail sai", _rj1.status_code == 200
    and len(_enviados) == _nj + 1, str(_rj1.status_code))
chk("vale a RESPOSTA do PagBank, não o que o aviso alega",
    _enviados[-1][2] == "gil@exemplo.com"
    and "Curso de Leitura de Mapa Natal está confirmada" in _enviados[-1][1])
chk("a consulta do pedido leva o token no cabeçalho, não na URL",
    _pb_json_chamadas and _pb_json_chamadas[-1][0].endswith(_OID)
    and _pb_json_chamadas[-1][1].get("Authorization")
    == "Bearer token-de-teste" and "token-de-teste" not in
    _pb_json_chamadas[-1][0])

_pb_resposta["r"] = _RespJSON(200, _pedido_pb(
    "ORDE_ZZZZ1111-BBBB-2222-CCCC-333344445555", "WAITING",
    "Curso de Leitura", email="hugo@exemplo.com"))
_nj2 = len(_enviados)
chk("pedido ainda não pago (boleto esperando) não libera",
    _cli.post("/pagbank-notificacao", json={
        "id": "ORDE_ZZZZ1111-BBBB-2222-CCCC-333344445555"}
    ).get_json().get("motivo") == "nao_pago" and len(_enviados) == _nj2)

_pb_resposta["r"] = _RespJSON(404, {})
chk("pedido que o PagBank não reconhece (aviso forjado): nada sai",
    _cli.post("/pagbank-notificacao", json={
        "id": "ORDE_FALSO111-BBBB-2222-CCCC-333344445555"}).status_code == 502
    and len(_enviados) == _nj2)
_nq2 = len(_pb_json_chamadas)
chk("evento que não é pedido (chargeback, custos) é ignorado sem consulta",
    _cli.post("/pagbank-notificacao", json={"id": "CHBK_123", "x": 1}
              ).get_json().get("motivo") == "formato"
    and len(_pb_json_chamadas) == _nq2)

# --- retrato: ver a FORMA do aviso sem espalhar dado de cliente -----------
_ret = _app._pagbank_retrato(_pedido_pb(_OID, "PAID", "Curso de Leitura",
                                        email="gil@exemplo.com",
                                        nome="Gilberto Sá"))
import json as _json3
_ret_txt = _json3.dumps(_ret, ensure_ascii=False)
chk("o retrato mostra o que eu preciso: id, status e nome do item",
    _OID in _ret_txt and "PAID" in _ret_txt
    and "Curso de Leitura" in _ret_txt)
chk("e NÃO mostra e-mail nem nome inteiro de cliente",
    "gil@exemplo.com" not in _ret_txt and "Gilberto" not in _ret_txt)
os.environ["PAGBANK_OBSERVAR"] = "1"
_pb_resposta["r"] = _RespJSON(404, {})
_no = len(_enviados)
_cli.post("/pagbank-notificacao", json={"id": "CHBK_999", "tipo": "x"})
chk("com PAGBANK_OBSERVAR=1, cada aviso vira um retrato para a equipe",
    len(_enviados) == _no + 1 and "retrato" in _enviados[-1][0])
os.environ["PAGBANK_OBSERVAR"] = ""
_no2 = len(_enviados)
_cli.post("/pagbank-notificacao", json={"id": "CHBK_999", "tipo": "x"})
chk("desligado, não manda nada", len(_enviados) == _no2)

# --- perguntar em vez de esperar: a busca e o diagnóstico -----------------
# O cadastro do aviso falhou dentro do PagBank. O servidor passa a poder
# OLHAR as vendas, e o diagnóstico mostra o que ele vê sem dado de cliente.
_BUSCA_XML = ("<transactionSearchResult><transactions>"
              "<transaction><date>2026-11-23T10:15:00.000-03:00</date>"
              "<code>TX-B1</code><status>3</status>"
              "<grossAmount>449.99</grossAmount>"
              "<paymentMethod><type>1</type></paymentMethod></transaction>"
              "<transaction><date>2026-11-22T09:00:00.000-03:00</date>"
              "<code>TX-B2</code><status>3</status>"
              "<grossAmount>350.00</grossAmount></transaction>"
              "</transactions></transactionSearchResult>")
_det = {"TX-B1": _xml_pb("TX-B1", "3", "Curso de Leitura de Mapa Natal",
                         email="ivo@exemplo.com", nome="Ivo Lins"),
        "TX-B2": _xml_pb("TX-B2", "3", "Consulta de Astroterapia",
                         email="lia@exemplo.com", nome="Lia Nunes")}
_busca_params = []


def _pb_get3(url, params=None, headers=None, timeout=None):
    if url.rstrip("/").endswith("/transactions"):
        _busca_params.append(dict(params or {}))
        return _RespPB(_pb_resposta.get("busca_http", 200), _BUSCA_XML)
    return _RespPB(200, _det[url.rsplit("/", 1)[1]])


_app.requests.get = _pb_get3
_app.PAGBANK_EMAIL, _app.PAGBANK_TOKEN = "conta@teste.com", "token-de-teste"
_txs, _err = _app._pagbank_buscar(horas=48)
chk("a busca devolve as vendas com código, status e valor",
    _err is None and [t["codigo"] for t in _txs] == ["TX-B1", "TX-B2"]
    and _txs[0]["status"] == "3")
chk("a janela pedida nunca termina no futuro (o PagBank recusa)",
    _busca_params[-1]["finalDate"] < __import__("datetime").datetime
    .utcnow().strftime("%Y-%m-%dT%H:%M"))

_CHJ = dict(_CH, **{"Content-Type": "application/json"})
chk("o diagnóstico exige a chave da API",
    _cli.post("/pagbank-diag", json={}).status_code == 401)
_nd = len(_enviados)
_dg = _cli.post("/pagbank-diag", json={"horas": 48}, headers=_CH).get_json()
chk("o diagnóstico mostra a descrição e o que foi reconhecido",
    _dg["status"] == "ok" and _dg["total"] == 2
    and _dg["vendas"][0]["itens"] == ["Curso de Leitura de Mapa Natal"]
    and _dg["vendas"][0]["curso"] == "leitura"
    and _dg["vendas"][1]["curso"] is None, str(_dg)[:300])
_dg_txt = _json3.dumps(_dg, ensure_ascii=False)
chk("e NÃO devolve nome nem e-mail de cliente, nem o token",
    "ivo@exemplo.com" not in _dg_txt and "Ivo" not in _dg_txt
    and "Lia" not in _dg_txt and "token-de-teste" not in _dg_txt)
chk("olhar não emite nem envia nada", len(_enviados) == _nd)
_esq = "\n".join(_dg["amostras"]["Curso de Leitura de Mapa Natal"])
chk("a FORMA da venda mostra os campos, com valor só onde é seguro",
    "items/item/description = Curso de Leitura de Mapa Natal" in _esq
    and "transaction/status = 3" in _esq
    and "sender/email = <oculto>" in _esq and "Ivo" not in _esq, _esq[:400])
chk("o resumo agrupa por descrição, valor e situação",
    {"item": "Curso de Leitura de Mapa Natal", "valor": "449.99",
     "status": "3", "quantas": 1} in _dg["resumo"])
_pb_resposta["busca_http"] = 401
_dg2 = _cli.post("/pagbank-diag", json={}, headers=_CH).get_json()
chk("token que o PagBank recusa aparece como recusa, não como 'zero vendas'",
    _dg2["status"] == "pagbank_recusou" and "401" in _dg2["detalhe"])
_pb_resposta["busca_http"] = 200

_app.PAGBANK_EMAIL, _app.PAGBANK_TOKEN = "", ""

# A busca por origem não pode depender de uma janela de N códigos.
for _i in range(210):
    _v2.emitir(V.ALUNA, origem=f"enchimento:{_i}")
chk("com mais de 200 códigos emitidos depois, o pedido antigo ainda acha "
    "o seu", any(l["usado_em"] is not None for l in _v2.por_origem(
        "leitura:bia@exemplo.com:pb-TX-0001")))

print()
if falhas:
    print(f">>> {falhas} FALHOU")
    raise SystemExit(1)
print("VOUCHER DE USO ÚNICO 18/11: TUDO PROVADO")
