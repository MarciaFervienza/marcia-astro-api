"""VOUCHERS — código de acesso de uso único (18/11).

POR QUE EXISTE. Até 18/11 a porta do formulário era UMA senha, igual
para todo mundo, distribuída por e-mail e destinada a circular dentro de
um PDF de presente. Senha que não expira e não conta usos não é tranca,
é aviso. E cada uso custa uma geração inteira: Claude, Pinecone,
geocoding, PDF, SendGrid.

ONDE A TRANCA MORA — e por que não na porta. A página do formulário pode
continuar com a senha do Wix ou sem ela; tanto faz. O que precisa ser
contado é o RELATÓRIO, porque é ele que custa dinheiro e é ele que a
pessoa recebe. Trancar a porta e deixar a geração livre é trancar a
janela errada. Por isso a checagem vive aqui, chamada do caminho de
geração, e não num gate de página.

RESERVA E DEVOLVE, não "marca no fim". O código é marcado como usado
ANTES de a geração começar, porque duas submissões simultâneas do mesmo
código precisam que exatamente UMA passe — e isso só é verdade se a
decisão for atômica e anterior ao trabalho. Marcar no fim gera os dois
relatórios e consome um código só.

Em troca, toda falha DEVOLVE o código: falha fechada de língua, exceção
no meio, worker morto. Marcar no começo sem devolver puniria a cliente
pela falha do sistema — ela ficaria sem relatório E sem código, e o
suporte disso cai na Márcia.

ALFABETO SEM AMBIGUIDADE. Sem 0/O, sem 1/I/L. O código vai ser lido de
um PDF e digitado à mão, provavelmente no celular. Cada par ambíguo é um
e-mail de suporte.

ENTRADA TOLERANTE. `mf 7kq2 r4xd`, `MF-7KQ2-R4XD` e `mf7kq2r4xd` são o
mesmo código. A diferença entre tolerar e não tolerar é a diferença
entre nenhum e-mail de dúvida e vários.
"""
import logging
import os
import secrets
import time

from fila import _Banco

logger = logging.getLogger("natal-api")

# Tipos de voucher. 'aluna' é o relatório da própria compradora;
# 'presente' é o que ela encaminha para quem quiser.
ALUNA = "aluna"
PRESENTE = "presente"

# Validade padrão. Cento e vinte dias cobre a venda de novembro, o curso
# começando em janeiro e a pessoa que deixou o e-mail parado na caixa.
VALIDADE_PADRAO_DIAS = 120

# Sem 0/O/1/I/L. Ver docstring.
_ALFABETO = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"

_ESQUEMA = [
    """
    CREATE TABLE IF NOT EXISTS vouchers (
        codigo       TEXT PRIMARY KEY,
        tipo         TEXT NOT NULL,
        origem       TEXT,
        criado_em    DOUBLE PRECISION NOT NULL,
        expira_em    DOUBLE PRECISION NOT NULL,
        usado_em     DOUBLE PRECISION,
        usado_por    TEXT,
        nome_gerado  TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_vouchers_origem ON vouchers (origem)",
    # Vendas já tratadas pelo caminho automático (PagBank). A chave
    # primária é a trava: ver `reivindicar`.
    """
    CREATE TABLE IF NOT EXISTS vendas_vistas (
        chave   TEXT PRIMARY KEY,
        quando  DOUBLE PRECISION NOT NULL
    )
    """,
]

# Motivos de recusa. São strings estáveis porque viram mensagem para a
# cliente e linha de painel para a Márcia — as duas coisas quebram se o
# motivo virar texto livre.
INEXISTENTE = "inexistente"
JA_USADO = "ja_usado"
EXPIRADO = "expirado"
OK = "ok"


def normalizar(codigo):
    """Forma canônica: só alfanumérico, maiúsculo.

    Guardada assim e comparada assim. O hífen é enfeite de leitura.
    """
    if not codigo:
        return ""
    return "".join(c for c in str(codigo).upper() if c.isalnum())


def formatar(codigo):
    """Forma de leitura: MF-XXXX-XXXX. Só para mostrar, nunca para comparar."""
    c = normalizar(codigo)
    if len(c) == 10 and c.startswith("MF"):
        return f"MF-{c[2:6]}-{c[6:]}"
    return c


def _sortear():
    corpo = "".join(secrets.choice(_ALFABETO) for _ in range(8))
    return "MF" + corpo


class Vouchers(_Banco):
    """Vouchers sobre Postgres (produção) ou SQLite (testes).

    Mesma camada `_Banco` da fila, de propósito: duas implementações de
    conexão seriam dois caminhos divergindo em silêncio, que é o defeito
    que a fila já documentou e pagou para aprender.
    """

    def __init__(self, dsn=None):
        super().__init__(dsn)
        if not self.dsn:
            raise RuntimeError(
                "DATABASE_URL ausente — os vouchers não podem abrir. NÃO há "
                "fallback para memória: voucher em memória é consumido duas "
                "vezes no primeiro restart do container, que é exatamente o "
                "evento contra o qual ele existe."
            )

    def criar_tabelas(self):
        return self.criar(_ESQUEMA)

    # ---------------------------------------------------------- emissão
    def emitir(self, tipo, origem=None, dias=VALIDADE_PADRAO_DIAS,
               agora=None):
        """Cria um código novo e devolve a forma de leitura.

        Colisão é tratada tentando de novo: 31^8 é grande o bastante para
        a colisão ser improvável e barato o bastante para não valer um
        laço elaborado. Três tentativas e desiste alto.
        """
        if tipo not in (ALUNA, PRESENTE):
            raise ValueError(f"tipo desconhecido: {tipo!r}")
        agora = agora if agora is not None else time.time()
        expira = agora + dias * 86400
        con = self.con()
        for _ in range(3):
            codigo = _sortear()
            try:
                con.cursor().execute(self._q(
                    "INSERT INTO vouchers (codigo, tipo, origem, criado_em, "
                    "expira_em) VALUES (%s, %s, %s, %s, %s)"),
                    (codigo, tipo, origem, agora, expira))
                if self.postgres:
                    con.commit()
                return formatar(codigo)
            except Exception:                           # noqa: BLE001
                if self.postgres:
                    con.rollback()
                continue
        raise RuntimeError("não consegui emitir um código único em 3 tentativas")

    # ---------------------------------------------------------- consumo
    def consumir(self, codigo, email=None, nome=None, agora=None):
        """Reserva o código para ESTA geração. Devolve (ok, motivo).

        ATÔMICO POR DESENHO. O UPDATE condicional é a decisão inteira:
        quem conseguir mudar a linha ganhou, quem não conseguir perdeu.
        A alternativa natural — SELECT para conferir, depois UPDATE para
        marcar — tem uma janela entre as duas instruções, e duas
        submissões simultâneas do mesmo código passam as duas pelo SELECT
        antes de qualquer UPDATE acontecer. É a mesma disciplina do
        `reivindicar` da fila, pelo mesmo motivo.

        O SELECT que vem depois existe só para EXPLICAR a recusa. Ele
        nunca decide nada.
        """
        c = normalizar(codigo)
        agora = agora if agora is not None else time.time()
        if not c:
            return False, INEXISTENTE
        con = self.con()
        cur = con.cursor()
        if self.postgres:
            cur.execute(
                "UPDATE vouchers SET usado_em=%s, usado_por=%s, nome_gerado=%s "
                "WHERE codigo=%s AND usado_em IS NULL AND expira_em > %s "
                "RETURNING tipo",
                (agora, email, nome, c, agora))
            ganhou = cur.fetchone() is not None
            con.commit()
        else:
            # SQLite é de escritor único; BEGIN IMMEDIATE pega a trava de
            # escrita ANTES de ler, que é o que torna o par indivisível.
            cur.execute("BEGIN IMMEDIATE")
            cur.execute(
                "UPDATE vouchers SET usado_em=?, usado_por=?, nome_gerado=? "
                "WHERE codigo=? AND usado_em IS NULL AND expira_em > ?",
                (agora, email, nome, c, agora))
            ganhou = cur.rowcount == 1
            cur.execute("COMMIT")
        if ganhou:
            logger.info("VOUCHER consumido %s por %r", formatar(c),
                        (email or "")[:60])
            return True, OK
        return False, self._motivo_da_recusa(c, agora)

    def _motivo_da_recusa(self, codigo_normalizado, agora):
        linha = self.consultar(codigo_normalizado)
        if linha is None:
            return INEXISTENTE
        if linha["usado_em"] is not None:
            return JA_USADO
        return EXPIRADO

    def devolver(self, codigo):
        """Desfaz a reserva. Chamado em TODA falha de geração.

        Só devolve o que está reservado: se a linha já estiver livre, não
        faz nada. Isso torna a chamada idempotente, e idempotência aqui
        importa porque o caminho de falha pode ser percorrido duas vezes
        (exceção dentro do tratamento de exceção já aconteceu neste
        projeto).
        """
        c = normalizar(codigo)
        if not c:
            return False
        con = self.con()
        cur = con.cursor()
        cur.execute(self._q(
            "UPDATE vouchers SET usado_em=NULL, usado_por=NULL, "
            "nome_gerado=NULL WHERE codigo=%s AND usado_em IS NOT NULL"), (c,))
        mexeu = cur.rowcount == 1
        if self.postgres:
            con.commit()
        if mexeu:
            logger.info("VOUCHER devolvido %s", formatar(c))
        return mexeu

    # ---------------------------------------------------------- leitura
    def consultar(self, codigo):
        c = normalizar(codigo)
        if not c:
            return None
        cur = self.con().cursor()
        cur.execute(self._q(
            "SELECT codigo, tipo, origem, criado_em, expira_em, usado_em, "
            "usado_por, nome_gerado FROM vouchers WHERE codigo=%s"), (c,))
        linha = cur.fetchone()
        if not linha:
            return None
        campos = ("codigo", "tipo", "origem", "criado_em", "expira_em",
                  "usado_em", "usado_por", "nome_gerado")
        return dict(zip(campos, linha))

    def por_origem(self, origem):
        """Todos os códigos emitidos para UMA origem, do mais novo ao mais
        antigo, usados ou não.

        Existe porque a busca por origem era feita varrendo `listar(200)`:
        com o 201º código emitido, a compra mais antiga sairia da janela
        e "já emiti para este pedido" passaria a responder "não" em
        silêncio, emitindo de novo.
        """
        if not origem:
            return []
        cur = self.con().cursor()
        cur.execute(self._q(
            "SELECT codigo, tipo, origem, criado_em, expira_em, usado_em, "
            "usado_por, nome_gerado FROM vouchers WHERE origem=%s "
            "ORDER BY criado_em DESC"), (origem,))
        campos = ("codigo", "tipo", "origem", "criado_em", "expira_em",
                  "usado_em", "usado_por", "nome_gerado")
        return [dict(zip(campos, l)) for l in cur.fetchall()]

    # ------------------------------------------------- vendas já tratadas
    def reivindicar(self, chave, agora=None):
        """Marca uma venda como MINHA. True só para quem chegou primeiro.

        ATÔMICO PELO MESMO MOTIVO de `consumir`: a decisão inteira é uma
        instrução só. "Olhar se já tratei e depois marcar" tem uma janela
        entre as duas, e nela a vigia e o aviso (ou dois servidores, na
        troca de versão) passam os dois e a aluna recebe dois e-mails. A
        chave primária não deixa a segunda inserção acontecer.
        """
        if not chave:
            return False
        con = self.con()
        cur = con.cursor()
        try:
            cur.execute(self._q(
                "INSERT INTO vendas_vistas (chave, quando) VALUES (%s, %s) "
                "ON CONFLICT (chave) DO NOTHING"),
                (str(chave), agora if agora is not None else time.time()))
            ganhou = cur.rowcount == 1
            if self.postgres:
                con.commit()
            return ganhou
        except Exception:                               # noqa: BLE001
            if self.postgres:
                con.rollback()
            raise

    def soltar(self, chave):
        """Desfaz a reivindicação (a venda volta a poder ser tratada)."""
        con = self.con()
        con.cursor().execute(self._q(
            "DELETE FROM vendas_vistas WHERE chave=%s"), (str(chave),))
        if self.postgres:
            con.commit()

    def listar(self, limite=50):
        cur = self.con().cursor()
        cur.execute(self._q(
            "SELECT codigo, tipo, origem, criado_em, expira_em, usado_em, "
            "usado_por, nome_gerado FROM vouchers "
            "ORDER BY criado_em DESC LIMIT %s"), (limite,))
        campos = ("codigo", "tipo", "origem", "criado_em", "expira_em",
                  "usado_em", "usado_por", "nome_gerado")
        return [dict(zip(campos, l)) for l in cur.fetchall()]


def abrir(dsn=None):
    v = Vouchers(dsn or os.environ.get("DATABASE_URL"))
    v.criar_tabelas()
    return v
