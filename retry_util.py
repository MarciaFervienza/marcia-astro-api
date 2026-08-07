"""Retry com backoff para chamadas externas — importado por app e por
report_generator. Módulo próprio porque app importa report_generator: um
import de volta fecharia ciclo, e duas cópias seriam a duplicação que já
nos custou quatro mentiras da fixture hoje (R3)."""
import logging
import random
import time as _time

logger = logging.getLogger("natal-api")


# =============================================================
# RETRY COM BACKOFF PARA CHAMADAS EXTERNAS (19/07)
#
# Em 19/07 a geolocalização devolveu 429 em três rodadas de teste, e o
# endpoint respondeu HTTP 400 "Erro ao consultar geolocalização" — um
# cliente que pagou veria isso. O Nominatim limita a 1 requisição por
# segundo; 429 é a resposta ESPERADA sob concorrência, não uma falha.
#
# O retry existia — no meu script de teste, não no produto. É a mesma
# classe de "o instrumento não é o produto" que já apareceu quatro vezes
# hoje na fixture. Aqui ele passa para o lado do servidor.
#
# Só erro TRANSITÓRIO é repetido. "Cidade não encontrada" é resposta
# definitiva: repetir só gastaria o tempo do cliente para dar o mesmo erro.
# =============================================================
_RETRY_SINAIS = (
    "429", "500", "502", "503", "504",
    "timed out", "timeout", "unavailable", "temporarily",
    "connection reset", "connection aborted", "too many requests",
    "bad gateway", "service unavailable",
)


def _erro_transitorio(exc):
    """True quando vale a pena repetir. Conservador: na dúvida, NÃO repete."""
    s = f"{type(exc).__name__} {exc}".lower()
    return any(sig in s for sig in _RETRY_SINAIS)


def _com_retry(fn, tentativas=4, base=1.0, rotulo="chamada externa", _sleep=None):
    """Executa fn() repetindo erros transitórios com backoff exponencial.

    Devolve (resultado, erro). Erro None em caso de sucesso. Nunca levanta.
    Esperas: 1s, 2s, 4s (+ jitter) — no pior caso ~7s, dentro de uma geração
    que já leva ~90s. O jitter evita que duas gerações simultâneas repitam
    no mesmo instante e tomem 429 de novo, que foi como o 429 apareceu.
    """
    dorme = _sleep or _time.sleep
    ultimo = None
    for k in range(tentativas):
        try:
            return fn(), None
        except Exception as exc:
            ultimo = exc
            if not _erro_transitorio(exc) or k == tentativas - 1:
                break
            espera = base * (2 ** k) + random.uniform(0.0, 0.4)
            logger.warning("%s: erro transitório (%s) — tentativa %d/%d, "
                           "aguardando %.1fs", rotulo, exc, k + 1, tentativas, espera)
            dorme(espera)
    return None, ultimo


