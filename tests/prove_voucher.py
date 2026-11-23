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
if falhas:
    print(f">>> {falhas} FALHOU")
    raise SystemExit(1)
print("VOUCHER DE USO ÚNICO 18/11: TUDO PROVADO")
