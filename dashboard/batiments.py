"""Page /batiments (mission « deux batiments », etape 4) : les batiments A (copie) et B (court terme) cote a cote.

Lecture seule. Chaque batiment est lu separement (zero melange) via la cle SSH en lecture seule :
    ssh ... root@VPS "batiment A /etat"     (commande forcee ajoutee par Code, proxy vers 127.0.0.1:8101/8102)
Mode essai sans conteneurs : BATIMENTS_FIXTURES=<dossier> contenant A.json et B.json
    {"etat": {...}, "trades": {...}, "wallets": [...], "chat": [...], "agents": [...], "lecons": {...},
     "rapport": {...}, "bougies": {...}}
Une route qui echoue garde sa derniere valeur avec son age ; jamais de valeur inventee.
"""
import json
import os
import re
import threading
import time
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
PAGE = HERE / "batiments.html"
FIXTURES = os.environ.get("BATIMENTS_FIXTURES")
BATIMENTS = ("A", "B")
ROUTES = ("etat", "trades", "wallets", "chat", "agents", "lecons", "rapport")
PERIODE = {"A": 15, "B": 5}                      # secondes entre deux lectures (carte etape 4)
CACHE_BOUGIES_S = 4
_SYMBOLE = re.compile(r"^[A-Z0-9]{2,20}$")
_TF = ("1m", "5m", "15m")

_cache: dict = {b: {} for b in BATIMENTS}          # {b: {route: {"data", "at", "erreur"}}}
_bougies: dict = {}                                 # {(b, sym, tf): {"data", "at"}}
_lock = threading.Lock()
_started = False


def _ssh_lire(batiment: str, route: str) -> object:
    from server import _ssh                         # meme cle forcee en lecture seule que le reste de la page
    return json.loads(_ssh(f"batiment {batiment} {route}", timeout=20))


def _fixture(batiment: str, route: str) -> object:
    d = json.loads((Path(FIXTURES) / f"{batiment}.json").read_text(encoding="utf-8"))
    return d.get(route.strip("/").split("?")[0])


def lire(batiment: str, route: str) -> object:
    return _fixture(batiment, route) if FIXTURES else _ssh_lire(batiment, route)


def _rafraichir(batiment: str) -> None:
    for route in ROUTES:
        try:
            data = lire(batiment, "/" + route)
            with _lock:
                _cache[batiment][route] = {"data": data, "at": time.time(), "erreur": None}
        except Exception as e:                      # garde l'ancienne valeur, note l'erreur
            with _lock:
                ancien = _cache[batiment].get(route, {})
                _cache[batiment][route] = {**ancien, "erreur": f"{type(e).__name__}: {str(e)[:160]}",
                                           "echec_at": time.time()}


def _boucle(batiment: str) -> None:
    while True:
        _rafraichir(batiment)
        time.sleep(PERIODE[batiment])


def start() -> None:
    global _started
    if _started:
        return
    _started = True
    for b in BATIMENTS:                              # un fil par batiment : l'un en panne ne bloque pas l'autre
        threading.Thread(target=_boucle, args=(b,), daemon=True, name=f"batiment_{b}").start()


def instantane(batiment: str) -> dict:
    now = time.time()
    with _lock:
        routes = {r: dict(v) for r, v in _cache[batiment].items()}
    for v in routes.values():
        v["age_s"] = round(now - v["at"], 1) if v.get("at") else None
    return {"batiment": batiment, "lu_at": now, "periode_s": PERIODE[batiment], "routes": routes}


def bougies(batiment: str, sym: str, tf: str) -> dict:
    cle = (batiment, sym, tf)
    c = _bougies.get(cle)
    if c and time.time() - c["at"] < CACHE_BOUGIES_S:
        return c["data"]
    data = lire(batiment, f"/bougies?symbol={sym}&tf={tf}") or {}
    _bougies[cle] = {"data": data, "at": time.time()}
    return data


def handle(path: str):
    """(code, corps, type) si le chemin concerne /batiments, sinon None."""
    u = urllib.parse.urlparse(path)
    if u.path in ("/batiments", "/batiments/"):
        return 200, PAGE.read_bytes(), "text/html; charset=utf-8"
    m = re.match(r"^/api/batiments/([AB])(?:/(bougies))?$", u.path)
    if not m:
        return None
    b = m.group(1)
    if not m.group(2):
        start()
        return 200, json.dumps(instantane(b), ensure_ascii=False, default=str).encode(), "application/json"
    q = urllib.parse.parse_qs(u.query)
    sym = (q.get("symbol", [""])[0] or "").upper()
    tf = q.get("tf", ["1m"])[0]
    if not _SYMBOLE.match(sym) or tf not in _TF or b != "B":
        return 400, b'{"erreur": "requete invalide"}', "application/json"
    try:
        return 200, json.dumps(bougies(b, sym, tf), default=str).encode(), "application/json"
    except Exception as e:
        return 502, json.dumps({"erreur": f"{type(e).__name__}: {str(e)[:160]}"}).encode(), "application/json"
