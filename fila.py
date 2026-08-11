"""FILA PERSISTIDA — geração assíncrona (19/07).

POR QUE EXISTE. O gatilho registrado no ESTADO foi cruzado: pior caso
medido de 272s contra o teto de 250s, e o proxy do Railway corta em 300s.
E o degrau 1 da recuperação (regenerar uma vez sozinho) empurraria para
perto de 500s — só é possível DEPOIS do assíncrono.

POR QUE PERSISTIDA, e não em processo. Fila em memória perde o trabalho
num restart do container — e restart é exatamente o evento contra o qual a
fila existe. Um deploy no meio de uma geração de 4 minutos hoje perde tudo.

BACKEND PLUGÁVEL, e o motivo é medição, não elegância: sem isto eu só
poderia testar contra um Postgres que não existe nesta máquina, e o
histórico deste projeto diz o que acontece quando eu testo com instrumento
que não é o produto. O SQLite roda a MESMA lógica nos testes; o Postgres
roda em produção. A única divergência é a cláusula de reivindicação, que é
justamente a parte que precisa de garantia do banco.

FALHA ALTA, NUNCA SILENCIOSA. Sem `DATABASE_URL`, `abrir()` levanta. Não
há fallback para memória: um fallback silencioso transformaria "a fila
protege contra restart" numa frase falsa, que é o modo de falha que este
projeto já viu vezes demais.
"""
import json
import logging
import os
import threading
import time
import uuid

logger = logging.getLogger("natal-api")

# Estados possíveis de um trabalho.
PENDENTE = "pendente"
PROCESSANDO = "processando"
OK = "ok"
FALHOU = "falhou"

# Um trabalho em PROCESSANDO cujo heartbeat envelheceu além disto é
# considerado órfão — o worker que o tinha morreu.
HEARTBEAT_MORTO_SEGS = 300

# Teto de RETOMADAS por morte de worker. Sem teto, um trabalho que sempre
# mata o worker vira laço infinito de restart — o container morre, sobe,
# pega o mesmo trabalho, morre de novo. Mesma disciplina do teto de
# tentativas do encanamento de língua.
TETO_RETOMADAS = 2

_ESQUEMA = [
    """
    CREATE TABLE IF NOT EXISTS trabalhos (
        id            TEXT PRIMARY KEY,
        estado        TEXT NOT NULL,
        payload       TEXT NOT NULL,
        tentativas    INTEGER NOT NULL DEFAULT 0,
        criado_em     DOUBLE PRECISION NOT NULL,
        atualizado_em DOUBLE PRECISION NOT NULL,
        worker_id     TEXT,
        heartbeat     DOUBLE PRECISION,
        motivo_falha  TEXT,
        markdown      TEXT,
        chart         TEXT,
        meta          TEXT,
        email         TEXT,
        nome          TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_trabalhos_estado ON trabalhos (estado)",
]


class _Banco:
    """Conexão por thread + tradução de dialeto, sobre Postgres ou SQLite.

    Base compartilhada pela Fila e pelo cache de cidades (geo_cache). É
    uma peça só de propósito: duas implementações de conexão seria a R3, a
    classe de defeito em que dois caminhos divergem em silêncio — e a
    conexão por thread abaixo nasceu de um defeito EXATAMENTE desses.
    """

    def __init__(self, dsn=None):
        self.dsn = dsn or os.environ.get("DATABASE_URL")
        self.postgres = bool(self.dsn) and self.dsn.startswith(
            ("postgres://", "postgresql://"))
        # CONEXÃO POR THREAD (19/07). Uma conexão compartilhada quebra em
        # concorrência — "cannot start a transaction within a transaction".
        # E não é só teste: o gunicorn roda com --threads 4, então o app
        # Flask é multithread em produção.
        #
        # O primeiro teste de concorrência PASSOU com três das quatro
        # threads mortas: a sobrevivente fez os 6 trabalhos sozinha e a
        # asserção "6 de 6" não provou nada. Teste que passa pelo motivo
        # errado é pior que teste ausente.
        self._local = threading.local()

    # ---------------------------------------------------------- conexão
    def _conectar(self):
        if self.postgres:
            import psycopg
            return psycopg.connect(self.dsn, autocommit=False)
        import sqlite3
        con = sqlite3.connect(self.dsn, isolation_level=None,
                              check_same_thread=False)
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def con(self):
        c = getattr(self._local, "con", None)
        if c is None:
            c = self._conectar()
            self._local.con = c
        return c

    def _ph(self, n):
        """Marcador de parâmetro: %s no Postgres, ? no SQLite."""
        return ", ".join(["%s" if self.postgres else "?"] * n)

    def _q(self, sql):
        return sql if self.postgres else sql.replace("%s", "?")

    def criar(self, esquema):
        """Aplica um esquema DDL, traduzindo o tipo que o SQLite não tem."""
        con = self.con()
        cur = con.cursor()
        for ddl in esquema:
            if not self.postgres:
                ddl = ddl.replace("DOUBLE PRECISION", "REAL")
            cur.execute(ddl)
        if self.postgres:
            con.commit()
        return True


class Fila(_Banco):
    """Fila sobre Postgres (produção) ou SQLite (testes).

    `dsn` vindo de DATABASE_URL escolhe Postgres; um caminho de arquivo ou
    ":memory:" escolhe SQLite.
    """

    def __init__(self, dsn=None):
        super().__init__(dsn)
        if not self.dsn:
            raise RuntimeError(
                "DATABASE_URL ausente — a fila persistida não pode abrir. "
                "NÃO há fallback para memória: uma fila em processo perde o "
                "trabalho no restart, que é o evento contra o qual ela existe."
            )

    def criar_tabelas(self):
        con = self.con()
        cur = con.cursor()
        for ddl in _ESQUEMA:
            if not self.postgres:
                ddl = ddl.replace("DOUBLE PRECISION", "REAL")
            cur.execute(ddl)
        if self.postgres:
            con.commit()
        return True

    # ---------------------------------------------------------- escrita
    def enfileirar(self, payload, nome=None, email=None):
        tid = uuid.uuid4().hex[:16]
        agora = time.time()
        con = self.con()
        con.cursor().execute(self._q(
            "INSERT INTO trabalhos (id, estado, payload, tentativas, "
            "criado_em, atualizado_em, nome, email) "
            "VALUES (%s, %s, %s, 0, %s, %s, %s, %s)"),
            (tid, PENDENTE, json.dumps(payload, ensure_ascii=False),
             agora, agora, nome, email))
        if self.postgres:
            con.commit()
        return tid

    def reivindicar(self, worker_id):
        """Pega UM trabalho pendente, atomicamente. None se não houver.

        No Postgres usa SELECT ... FOR UPDATE SKIP LOCKED — é isso que
        impede dois workers pegarem o mesmo trabalho. No SQLite (testes) a
        serialização vem do próprio banco, que é de escritor único.
        """
        con = self.con()
        cur = con.cursor()
        agora = time.time()
        if self.postgres:
            cur.execute(
                "UPDATE trabalhos SET estado=%s, worker_id=%s, heartbeat=%s, "
                "atualizado_em=%s, tentativas=tentativas+1 "
                "WHERE id = (SELECT id FROM trabalhos WHERE estado=%s "
                "            ORDER BY criado_em LIMIT 1 "
                "            FOR UPDATE SKIP LOCKED) "
                "RETURNING id, payload, tentativas",
                (PROCESSANDO, worker_id, agora, agora, PENDENTE))
            linha = cur.fetchone()
            con.commit()
        else:
            cur.execute("BEGIN IMMEDIATE")
            cur.execute("SELECT id, payload, tentativas FROM trabalhos "
                        "WHERE estado=? ORDER BY criado_em LIMIT 1", (PENDENTE,))
            linha = cur.fetchone()
            if linha:
                cur.execute(
                    "UPDATE trabalhos SET estado=?, worker_id=?, heartbeat=?, "
                    "atualizado_em=?, tentativas=tentativas+1 WHERE id=?",
                    (PROCESSANDO, worker_id, agora, agora, linha[0]))
                linha = (linha[0], linha[1], linha[2] + 1)
            cur.execute("COMMIT")
        if not linha:
            return None
        return {"id": linha[0], "payload": json.loads(linha[1]),
                "tentativas": linha[2]}

    def devolver(self, tid, worker_id):
        """Solta um trabalho reivindicado por engano, SEM gastar tentativa.

        Existe por um defeito real (19/07): o /diag-fila roda quatro
        threads chamando `reivindicar`, e `reivindicar` devolve QUALQUER
        pendente — inclusive trabalho de cliente. A thread via que o id
        não era dela e simplesmente retornava, deixando o pedido preso em
        PROCESSANDO sem worker nenhum atrás dele. Dois pedidos reais
        ficaram travados assim, e só sairiam de lá quando algum worker
        subisse e o `retomar_orfaos` os alcançasse cinco minutos depois.

        Desfazer a tentativa é parte do contrato, não refinamento: sem
        isso, cada diagnóstico consumiria uma das DUAS retomadas do teto,
        e três diagnósticos matariam um trabalho que nunca falhou.
        """
        con = self.con()
        con.cursor().execute(self._q(
            "UPDATE trabalhos SET estado=%s, worker_id=NULL, heartbeat=NULL, "
            "atualizado_em=%s, "
            "tentativas=CASE WHEN tentativas > 0 THEN tentativas - 1 ELSE 0 END "
            "WHERE id=%s AND worker_id=%s AND estado=%s"),
            (PENDENTE, time.time(), tid, worker_id, PROCESSANDO))
        if self.postgres:
            con.commit()

    def heartbeat(self, tid, worker_id):
        con = self.con()
        con.cursor().execute(self._q(
            "UPDATE trabalhos SET heartbeat=%s WHERE id=%s AND worker_id=%s"),
            (time.time(), tid, worker_id))
        if self.postgres:
            con.commit()

    def concluir(self, tid, markdown=None, chart=None, meta=None):
        """Guarda os ARTEFATOS junto — é deles que o degrau 3 vive.

        Sem markdown e chart persistidos não há edição manual possível:
        hoje nada sobrevive ao fim da requisição.
        """
        con = self.con()
        con.cursor().execute(self._q(
            "UPDATE trabalhos SET estado=%s, atualizado_em=%s, markdown=%s, "
            "chart=%s, meta=%s WHERE id=%s"),
            (OK, time.time(), markdown,
             json.dumps(chart, ensure_ascii=False, default=str) if chart else None,
             json.dumps(meta, ensure_ascii=False, default=str) if meta else None,
             tid))
        if self.postgres:
            con.commit()

    def falhar(self, tid, motivo, markdown=None, chart=None, meta=None):
        con = self.con()
        con.cursor().execute(self._q(
            "UPDATE trabalhos SET estado=%s, atualizado_em=%s, "
            "motivo_falha=%s, markdown=COALESCE(%s, markdown), "
            "chart=COALESCE(%s, chart), meta=COALESCE(%s, meta) WHERE id=%s"),
            (FALHOU, time.time(), str(motivo)[:2000], markdown,
             json.dumps(chart, ensure_ascii=False, default=str) if chart else None,
             json.dumps(meta, ensure_ascii=False, default=str) if meta else None,
             tid))
        if self.postgres:
            con.commit()

    def retomar_orfaos(self, agora=None):
        """Trabalho em PROCESSANDO com heartbeat velho: o worker morreu.

        Volta para PENDENTE — a menos que já tenha estourado o teto, e aí
        vira FALHOU. Devolve (retomados, desistidos).
        """
        agora = agora if agora is not None else time.time()
        corte = agora - HEARTBEAT_MORTO_SEGS
        con = self.con()
        cur = con.cursor()
        cur.execute(self._q(
            "SELECT id, tentativas FROM trabalhos WHERE estado=%s "
            "AND (heartbeat IS NULL OR heartbeat < %s)"), (PROCESSANDO, corte))
        orfaos = cur.fetchall()
        retomados, desistidos = [], []
        for tid, tent in orfaos:
            if tent >= TETO_RETOMADAS:
                cur.execute(self._q(
                    "UPDATE trabalhos SET estado=%s, motivo_falha=%s, "
                    "atualizado_em=%s WHERE id=%s"),
                    (FALHOU, f"worker morreu {tent}x — teto de "
                             f"{TETO_RETOMADAS} retomadas atingido",
                     agora, tid))
                desistidos.append(tid)
            else:
                cur.execute(self._q(
                    "UPDATE trabalhos SET estado=%s, worker_id=NULL, "
                    "heartbeat=NULL, atualizado_em=%s WHERE id=%s"),
                    (PENDENTE, agora, tid))
                retomados.append(tid)
        if self.postgres:
            con.commit()
        if retomados or desistidos:
            logger.warning("fila: %d retomado(s), %d desistido(s) por morte "
                           "de worker", len(retomados), len(desistidos))
        return retomados, desistidos

    # ---------------------------------------------------------- leitura
    def buscar(self, tid):
        cur = self.con().cursor()
        cur.execute(self._q(
            "SELECT id, estado, tentativas, criado_em, atualizado_em, "
            "motivo_falha, markdown, chart, meta, nome, email, payload "
            "FROM trabalhos WHERE id=%s"), (tid,))
        r = cur.fetchone()
        if not r:
            return None
        return {"id": r[0], "estado": r[1], "tentativas": r[2],
                "criado_em": r[3], "atualizado_em": r[4], "motivo_falha": r[5],
                "markdown": r[6],
                "chart": json.loads(r[7]) if r[7] else None,
                "meta": json.loads(r[8]) if r[8] else None,
                "nome": r[9], "email": r[10],
                "payload": json.loads(r[11]) if r[11] else None}

    def falhados_desde(self, desde):
        """Para o RESUMO DIÁRIO. Sem a resposta HTTP como canal, este resumo
        é a ÚNICA rede contra falha silenciosa — se o alerta se perder, é
        aqui que ela aparece."""
        cur = self.con().cursor()
        cur.execute(self._q(
            "SELECT id, nome, email, motivo_falha, atualizado_em "
            "FROM trabalhos WHERE estado=%s AND atualizado_em >= %s "
            "ORDER BY atualizado_em"), (FALHOU, desde))
        return [{"id": a, "nome": b, "email": c, "motivo": d, "quando": e}
                for a, b, c, d, e in cur.fetchall()]

    def contagem_por_estado(self):
        cur = self.con().cursor()
        cur.execute("SELECT estado, COUNT(*) FROM trabalhos GROUP BY estado")
        return dict(cur.fetchall())
