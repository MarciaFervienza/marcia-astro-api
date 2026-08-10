"""WORKER — consome a fila e gera os relatórios (19/07).

Roda como serviço SEPARADO no Railway, mesmo repositório, comando
diferente:  `python worker.py`

Por que separado do web: o processo web tem de responder em 1s (o 202). O
trabalho leva 2 a 5 minutos. No mesmo processo, uma geração longa ocuparia
uma thread do gunicorn e degradaria o tempo de resposta de todo mundo.

O worker NÃO tem resposta HTTP para reportar erro — ninguém está
escutando. Por isso o alerta ao executivo@ deixa de ser redundância e vira
o ÚNICO canal, e por isso ele tem retry próprio e um resumo diário por
trás. Ver `_alerta_com_retry` e `resumo_diario`.
"""
import logging
import os
import socket
import threading
import time

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("natal-api")

INTERVALO_OCIOSO = 5          # espera entre sondagens quando a fila está vazia
INTERVALO_HEARTBEAT = 30      # bate o heartbeat a cada 30s durante o trabalho
INTERVALO_MANUTENCAO = 60     # retomada de órfãos e resumo diário


def _worker_id():
    return f"{socket.gethostname()}-{os.getpid()}"


def _bate_heartbeat(fila, tid, wid, parar):
    """Thread que mantém o heartbeat enquanto o trabalho roda.

    Sem isto, uma geração de 4 minutos pareceria órfã depois de 5 e seria
    retomada por outro worker — dois relatórios para a mesma cliente.
    """
    while not parar.wait(INTERVALO_HEARTBEAT):
        try:
            fila.heartbeat(tid, wid)
        except Exception as exc:
            logger.warning("heartbeat falhou para %s: %s", tid, exc)


def processar_um(fila, trabalho, wid):
    """Gera, monta o PDF e envia. Guarda os artefatos SEMPRE — inclusive na
    falha, porque é deles que o degrau 3 (edição manual) vive."""
    import app as _app
    tid = trabalho["id"]
    parar = threading.Event()
    bat = threading.Thread(target=_bate_heartbeat,
                           args=(fila, tid, wid, parar), daemon=True)
    bat.start()
    try:
        r = _app.executar_geracao(trabalho["payload"])
        if r.get("falha_lingua"):
            fila.falhar(tid, r["falha_lingua"], markdown=r.get("markdown"),
                        chart=r.get("chart"), meta=r.get("meta"))
            _app.alertar_falha_de_trabalho(tid, trabalho["payload"], r)
            return "falhou"
        fila.concluir(tid, markdown=r.get("markdown"), chart=r.get("chart"),
                      meta=r.get("meta"))
        return "ok"
    except Exception as exc:
        logger.exception("trabalho %s levantou", tid)
        fila.falhar(tid, f"exceção no worker: {exc}")
        try:
            _app.alertar_falha_de_trabalho(tid, trabalho["payload"],
                                           {"falha_lingua": str(exc)[:400]})
        except Exception:
            pass
        return "erro"
    finally:
        parar.set()


def manutencao(fila, estado):
    """Retomada de órfãos + resumo diário. Roda a cada minuto."""
    try:
        fila.retomar_orfaos()
    except Exception as exc:
        logger.warning("retomada de órfãos falhou: %s", exc)
    try:
        import app as _app
        _app.talvez_resumo_diario(fila, estado)
    except Exception as exc:
        logger.warning("resumo diário falhou: %s", exc)


def main():
    import fila as _fila
    f = _fila.Fila()
    f.criar_tabelas()
    wid = _worker_id()
    estado = {"ultimo_resumo": 0.0}
    logger.info("worker %s no ar", wid)
    ultima_manut = 0.0
    while True:
        agora = time.time()
        if agora - ultima_manut > INTERVALO_MANUTENCAO:
            manutencao(f, estado)
            ultima_manut = agora
        try:
            t = f.reivindicar(wid)
        except Exception as exc:
            logger.warning("reivindicar falhou: %s", exc)
            time.sleep(INTERVALO_OCIOSO)
            continue
        if not t:
            time.sleep(INTERVALO_OCIOSO)
            continue
        t0 = time.time()
        desfecho = processar_um(f, t, wid)
        logger.info("trabalho %s: %s em %.0fs (tentativa %d)",
                    t["id"], desfecho, time.time() - t0, t["tentativas"])


if __name__ == "__main__":
    main()
