"""CACHE DE GEOCODIFICAÇÃO NO POSTGRES.

POR QUE EXISTE (decisão da Márcia, 11/08). A queda para o Photon é
solução de dia: o bloqueio do Nominatim de hoje era temporário — passou
sozinho em cerca de uma hora — e isso significa que ele VAI VOLTAR. Trocar
de provedor não muda a natureza do problema; muda de quem é o limite.

O cache muda: cidade consultada uma vez fica guardada. As mesmas cidades
se repetem entre clientes, e é o mesmo conjunto pequeno de capitais que
aparece o tempo todo. Isso derruba o volume a quase nada, vale para
QUALQUER provedor atrás, e é o que torna o autocomplete possível sem
garantir bloqueio — o autocomplete consulta a cada digitação, e é ele que
mais provoca o limite.

SEM TTL, de propósito. Cidade não muda de lugar. O que pode mudar é o
rótulo no OpenStreetMap, e rótulo desatualizado é cosmético; coordenada
errada não é. Se um dia precisar limpar, é um DELETE.

Só guarda resposta NÃO VAZIA. Uma consulta sem resultado ("Belo Horizont"
no meio da digitação, ou uma cidade que o OSM ainda não tem) não vira
verdade permanente — o custo de repetir uma consulta que falha é uma
consulta; o custo de congelar uma ausência é uma cidade que nunca mais é
encontrada.

Degrada em silêncio: sem DATABASE_URL, ou com o banco fora, a
geocodificação segue funcionando sem cache. O cache é economia, não
correção — se ele virar dependência dura, uma queda do Postgres derruba
a geração, que é trocar um problema por um pior.
"""
import json
import logging
import re
import time
import unicodedata

from fila import _Banco

logger = logging.getLogger("natal-api")

_ESQUEMA = [
    """
    CREATE TABLE IF NOT EXISTS cidades_cache (
        chave      TEXT PRIMARY KEY,
        consulta   TEXT NOT NULL,
        resultados TEXT NOT NULL,
        provedor   TEXT,
        criado_em  DOUBLE PRECISION NOT NULL,
        usos       INTEGER NOT NULL DEFAULT 0,
        usado_em   DOUBLE PRECISION
    )
    """,
]


def normaliza(q):
    """Chave de busca: sem acento, sem caixa, sem espaço sobrando.

    Normalizar acento é ganho real e não risco: "sao paulo" e "São Paulo"
    são a MESMA consulta, e quem digita no formulário raramente acentua.
    O que NÃO se normaliza é pontuação de separação — "Santa Rosa, RS" e
    "Santa Rosa" são consultas diferentes e têm de continuar sendo, senão
    o cache passaria por cima da desambiguação por estado.
    """
    s = unicodedata.normalize("NFD", (q or "").strip().lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s)


class CacheCidades(_Banco):

    def criar_tabelas(self):
        return self.criar(_ESQUEMA)

    def buscar(self, q):
        """[{lat,lng,rotulo}] guardado, ou None se não houver."""
        cur = self.con().cursor()
        cur.execute(self._q("SELECT resultados FROM cidades_cache "
                            "WHERE chave=%s"), (normaliza(q),))
        linha = cur.fetchone()
        if not linha:
            return None
        try:
            res = json.loads(linha[0])
        except Exception:
            return None
        return res or None

    def marcar_uso(self, q):
        """Contador de acertos — é o número que diz se o cache vale.

        Separado da leitura e tolerante a falha: um erro ao contar não
        pode derrubar uma geração. Estatística nunca vale mais que o
        produto.
        """
        try:
            con = self.con()
            con.cursor().execute(self._q(
                "UPDATE cidades_cache SET usos=usos+1, usado_em=%s "
                "WHERE chave=%s"), (time.time(), normaliza(q)))
            if self.postgres:
                con.commit()
        except Exception as exc:
            logger.warning("cache de cidades: contador falhou (%s)", exc)

    def guardar(self, q, resultados, provedor=None):
        if not resultados:
            return False
        chave = normaliza(q)
        con = self.con()
        cur = con.cursor()
        dados = (chave, (q or "").strip(),
                 json.dumps(resultados, ensure_ascii=False),
                 provedor, time.time())
        if self.postgres:
            cur.execute(
                "INSERT INTO cidades_cache "
                "(chave, consulta, resultados, provedor, criado_em) "
                "VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (chave) DO NOTHING", dados)
            con.commit()
        else:
            cur.execute(
                "INSERT OR IGNORE INTO cidades_cache "
                "(chave, consulta, resultados, provedor, criado_em) "
                "VALUES (?, ?, ?, ?, ?)", dados)
        return True

    def estatisticas(self):
        cur = self.con().cursor()
        cur.execute("SELECT COUNT(*), COALESCE(SUM(usos), 0) "
                    "FROM cidades_cache")
        n, usos = cur.fetchone()
        return {"cidades_guardadas": int(n or 0),
                "consultas_evitadas": int(usos or 0)}


_CACHE = None
_TENTOU = False


def cache():
    """A instância, ou None se o banco não estiver disponível.

    Tenta UMA vez. Sem isto, um Postgres fora do ar transformaria cada
    geocodificação numa tentativa de conexão com timeout — o cache
    existe para diminuir latência, não para acrescentar.
    """
    global _CACHE, _TENTOU
    if _CACHE is not None or _TENTOU:
        return _CACHE
    _TENTOU = True
    try:
        c = CacheCidades()
        if not c.dsn:
            logger.info("cache de cidades desligado: sem DATABASE_URL")
            return None
        c.criar_tabelas()
        _CACHE = c
        logger.info("cache de cidades no ar")
    except Exception as exc:
        logger.warning("cache de cidades indisponível (%s) — seguindo sem", exc)
    return _CACHE
