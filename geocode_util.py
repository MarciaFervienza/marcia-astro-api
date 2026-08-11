"""GEOCODIFICAÇÃO COM QUEDA — Nominatim primeiro, Photon quando ele bloqueia.

POR QUE EXISTE (19/07). A fumaça contra produção falhou com HTTP 400,
"Non-successful status code 429". Não era limite passageiro nem falta de
retry: o retry está no produto, `429` está na lista de transitórios, e
ele repetiu 4 vezes com backoff antes de desistir.

O diagnóstico veio de uma comparação, não de leitura de código:

  · do Railway  → 429 em toda consulta, inclusive 90s depois de parar;
  · da máquina da Márcia → HTTP 200 imediato, com as coordenadas EXATAS
    que o app já usava (-19.9227318, -43.9450948 para Belo Horizonte).

Mesmo código, mesmo User-Agent, mesma cidade. O que muda é o IP: o
Nominatim bloqueia IP de datacenter por política. Isso não expira com
espera nem melhora com retry, e derrubava TUDO — geração de cliente novo
e o /buscar-cidade do autocomplete. Só um city_id já emitido escapava.

DECISÃO (Márcia, 19/07): Nominatim primeiro, Photon como queda. O Photon
lê A MESMA BASE (OpenStreetMap) por outra porta — não é outra opinião
sobre onde as cidades ficam, é o mesmo banco com outra entrada. Enquanto
o Nominatim responder, nada muda em relação aos 7 mapas já validados.

Medido contra as coordenadas que a Márcia verificou:

    Belo Horizonte 0,0 km · São Paulo 0,0 km · Curitiba 0,2 km
    Santa Rosa/RS  0,7 km · Porto Alegre 1,2 km · Rio 3,8 km

(O Open-Meteo foi medido junto e REPROVADO: errou Santa Rosa/RS por
17.855 km — foi para outro continente. É exatamente o caso que já
produziu um mapa errado com aparência perfeita.)

Todo resultado carrega `provedor`. Coordenada sem procedência é
coordenada que ninguém consegue auditar depois.
"""
import logging

from retry_util import _com_retry

logger = logging.getLogger("natal-api")

USER_AGENT = "marcia-astro-api/1.0"

# Ordem da cadeia. A primeira que responder ganha; a queda só é tentada
# quando a anterior falha por erro (não quando ela responde "não achei" —
# "cidade inexistente" é resposta definitiva, e insistir noutro provedor
# só trocaria um erro claro por uma coincidência).
PROVEDORES = ("nominatim", "photon")


def _via_nominatim(q, limit):
    from geopy.geocoders import Nominatim
    geo = Nominatim(user_agent=USER_AGENT, timeout=15)
    res, exc = _com_retry(
        lambda: geo.geocode(q, language="pt", exactly_one=False,
                            limit=limit, addressdetails=True),
        rotulo="geolocalização (Nominatim)")
    if exc is not None:
        raise exc
    return [{"lat": float(l.latitude), "lng": float(l.longitude),
             "rotulo": l.address} for l in (res or [])]


def _rotulo_photon(p):
    """Rótulo no MESMO formato do Nominatim: termina no país.

    `_ambiguidade_real` parte o rótulo por vírgula e lê a ÚLTIMA parte
    como país. Um rótulo que termine noutra coisa faria a detecção de
    ambiguidade calar — e ela é o que impede o "Santa Rosa → Califórnia".
    """
    partes = [p.get("name"), p.get("state") or p.get("county"),
              p.get("country")]
    return ", ".join(x for x in partes if x)


# LUGAR HABITADO. O filtro é ESTRITO de propósito: sem ele, o Photon
# devolveu "Simba Motors USA LLC" como primeira resposta para "Atlanta,
# Georgia, USA" — uma oficina mecânica, com coordenada plausível e
# aparência perfeita. Um mapa levantado sobre isso não teria como ser
# percebido por ninguém. Nunca aceitar POI: preferimos erro visível.
_TIPOS_LUGAR = {"city", "town", "village", "district", "municipality",
                "locality", "county", "hamlet", "borough"}


def _photon_cru(q, limit):
    import json
    import urllib.parse
    import urllib.request
    # SEM parâmetro de idioma, de propósito. `lang=pt` devolve HTTP 400 —
    # o Photon não aceita português (só en/de/fr). Medido, não suposto: a
    # primeira versão mandava lang=pt e teria dado 400 em TODA chamada da
    # queda, ou seja, proteção morta exatamente no dia em que o Nominatim
    # caísse. Sem o parâmetro ele devolve o nome nativo — "Brasil", que é
    # o que a cliente lê; com lang=en viria "Brazil".
    u = ("https://photon.komoot.io/api/?limit=" + str(limit)
         + "&q=" + urllib.parse.quote(q))
    req = urllib.request.Request(u, headers={"User-Agent": USER_AGENT})

    def _bate():
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r)

    d, exc = _com_retry(_bate, rotulo="geolocalização (Photon)")
    if exc is not None:
        raise exc
    return filtra_lugares(d)


def filtra_lugares(d):
    """Só lugares habitados, a partir da resposta CRUA do Photon.

    Separada da chamada de rede para poder ser provada offline com uma
    resposta real gravada. É a peça mais perigosa do módulo — quem falha
    aqui entrega a coordenada de uma loja como cidade de nascimento — e a
    peça mais perigosa não pode ter teste que dependa de o Photon estar
    no ar no dia do gate.
    """
    out = []
    for f in (d or {}).get("features", []):
        c = (f.get("geometry") or {}).get("coordinates") or []
        p = f.get("properties") or {}
        if len(c) != 2 or p.get("osm_key") != "place":
            continue
        if p.get("type") not in _TIPOS_LUGAR:
            continue
        rot = _rotulo_photon(p)
        if not rot:
            continue
        out.append({"lat": float(c[1]), "lng": float(c[0]), "rotulo": rot})
    return out


def _via_photon(q, limit):
    """Photon, soltando o último segmento se a consulta inteira não achar.

    Por quê: o token literal "USA" ENVENENA o Photon — ele casa com nomes
    de comércio ("Simba Motors USA LLC") e nenhum lugar habitado sobra.
    Medido: "Atlanta, Georgia, USA" → nada; "Atlanta, Georgia" → 0,0 km
    do valor verificado. Vale igual para "New York, NY, USA" e "Miami,
    Florida, USA" — não é o caso Atlanta, é a classe.

    Soltar o último segmento é a correção da CLASSE, não uma tabela de
    abreviações: funciona para qualquer sufixo que o Photon não saiba
    ler. A consulta fica menos específica, mas não menos segura — quem
    protege contra homônima é `_ambiguidade_real`, que segue rodando
    sobre o resultado e RECUSA quando aparece mais de um país.
    """
    partes = [p.strip() for p in q.split(",") if p.strip()]
    # No máximo duas soltas: além disso sobra só o nome da cidade, e aí a
    # ambiguidade é grande demais para valer o palpite.
    for corte in range(0, min(2, max(0, len(partes) - 1)) + 1):
        tentativa = ", ".join(partes[:len(partes) - corte]) if corte else q
        res = _photon_cru(tentativa, limit)
        if res:
            if corte:
                logger.warning("Photon: %r não achou lugar; resolvido como "
                               "%r (soltou %d segmento(s))", q[:60],
                               tentativa[:60], corte)
            return res
    return []


_IMPL = {"nominatim": _via_nominatim, "photon": _via_photon}


def buscar_bruto(q, limit=6):
    """[{lat, lng, rotulo}], provedor, erro — percorrendo a cadeia.

    Devolve no primeiro provedor que RESPONDER, mesmo que a resposta seja
    vazia: lista vazia é "não existe essa cidade", que é definitiva.
    Só erro faz cair para o próximo.
    """
    q = (q or "").strip()
    if not q:
        return [], None, "Consulta vazia."

    # CACHE PRIMEIRO. Ver geo_cache: a queda de provedor é solução de dia
    # — o bloqueio de hoje passou sozinho, o que significa que volta. O
    # que realmente derruba o volume é não perguntar duas vezes. Falha do
    # cache NUNCA derruba a geocodificação: é economia, não correção.
    _c = None
    try:
        import geo_cache
        _c = geo_cache.cache()
        if _c is not None:
            guardado = _c.buscar(q)
            if guardado:
                _c.marcar_uso(q)
                return guardado, "cache", None
    except Exception as exc:                           # noqa: BLE001
        logger.warning("cache de cidades falhou na leitura (%s) — seguindo", exc)
        _c = None

    erros = []
    for nome in PROVEDORES:
        try:
            res = _IMPL[nome](q, limit)
        except Exception as exc:                       # noqa: BLE001
            erros.append(f"{nome}: {exc}")
            logger.warning("geocoding: %s falhou (%s) — tentando o próximo",
                           nome, exc)
            continue
        if erros:
            # Queda EXERCITADA. Fica no log com nome porque uma coordenada
            # vinda da queda tem de ser rastreável meses depois.
            logger.warning("geocoding: %r resolvido por %s após falha de %s",
                           q[:60], nome, "; ".join(erros)[:200])
        if _c is not None and res:
            try:
                _c.guardar(q, res, nome)
            except Exception as exc:                   # noqa: BLE001
                logger.warning("cache de cidades falhou ao guardar (%s)", exc)
        return res, nome, None
    return [], None, ("Erro ao consultar geolocalização: "
                      + " | ".join(erros)[:300])
