"""SecurityAgent (user brief §3): on-chain safety check with a VETO before any trade on a DEX token
(memecoins, coins spotted through a wallet). Public APIs only, nothing is signed, no wallet.

check(chain, token) -> (ok, reasons, details); one agent_events line (agent SecurityAgent, action
APPROVE or VETO) per new verdict; verdicts cached 15 min. EVERY rule must pass:
- DexScreener: best pair liquidity >= MIN_LIQ_USD, pair older than MIN_AGE_H
- Solana: RugCheck (mint and freeze authority revoked, LP locked >= 90 %, top-10 holders outside the
  pools <= 30 %, transfer fee < 5 %, not flagged rugged, no "danger" risk)
- EVM (ethereum, bsc, base, arbitrum, polygon): GoPlus (not a honeypot, can sell all, buy/sell tax
  < 5 %, not mintable, owner cannot change balances, top-10 holders <= 30 %, LP locked >= 90 %) and
  honeypot.is (simulated buy + sell must succeed, sell tax < 5 %)
Any API failure = VETO (fail-closed): we never trade a token we could not check.

details also carries creator_pct (share of the supply still held by the token creator, None when unknown).
It is information, not a rule here: each caller applies its own limit (MemeHunter vetoes above
MEME_MAX_CREATOR_PCT and exits when the creator sells). creator_pct(chain, token) re-reads it, uncached.
"""
import time

import requests

from agent_events import log_event

MIN_LIQ_USD, MIN_AGE_H, MAX_TOP10, MAX_TAX, MIN_LP_LOCK = 100_000, 24, 0.30, 0.05, 0.90
CACHE_S = 900
GOPLUS_CHAIN = {"ethereum": "1", "bsc": "56", "base": "8453", "arbitrum": "42161", "polygon": "137"}
HONEYPOT_CHAIN = {"ethereum": 1, "bsc": 56, "base": 8453, "arbitrum": 42161}
_cache: dict[tuple, tuple[float, tuple]] = {}


def _dex(chain: str, token: str, min_age_h: float = MIN_AGE_H, min_liq_usd: float = MIN_LIQ_USD) -> tuple[list[str], dict]:
    r = requests.get(f"https://api.dexscreener.com/tokens/v1/{chain}/{token}", timeout=15)
    pairs = [p for p in (r.json() if r.status_code == 200 else []) if (p.get("liquidity") or {}).get("usd")]
    if not pairs:
        return ["aucune paire DEX trouvee"], {}
    best = max(pairs, key=lambda p: p["liquidity"]["usd"])
    liq = float(best["liquidity"]["usd"])
    age_h = (time.time() * 1000 - float(best.get("pairCreatedAt") or 0)) / 3.6e6
    bad = []
    if liq < min_liq_usd:
        bad.append(f"liquidite {liq:,.0f} $ < {min_liq_usd:,.0f} $")
    if age_h < min_age_h:
        bad.append(f"paire agee de {age_h:.0f} h < {min_age_h:g} h")
    return bad, {"pair": best.get("pairAddress"), "liq_usd": round(liq), "age_h": round(age_h, 1),
                 "symbol": (best.get("baseToken") or {}).get("symbol")}


def _creator_pct_rugcheck(d: dict) -> float | None:
    supply = float((d.get("token") or {}).get("supply") or 0)
    if not d.get("creator") or supply <= 0:
        return None
    return float(d.get("creatorBalance") or 0) / supply


def _creator_pct_goplus(d: dict) -> float | None:
    raw = d.get("creator_percent")
    try:
        return float(raw) if raw not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _rugcheck_report(mint: str) -> requests.Response:
    return requests.get(f"https://api.rugcheck.xyz/v1/tokens/{mint}/report", timeout=20)


def _goplus_token(chain: str, addr: str) -> dict:
    r = requests.get(f"https://api.gopluslabs.io/api/v1/token_security/{GOPLUS_CHAIN[chain]}",
                     params={"contract_addresses": addr}, timeout=20)
    return (r.json().get("result") or {}).get(addr.lower()) or {}


def creator_pct(chain: str, token: str) -> float | None:
    """Share of the supply the creator still holds right now (uncached); None = unknown, never a verdict."""
    try:
        if chain == "solana":
            r = _rugcheck_report(token)
            return _creator_pct_rugcheck(r.json()) if r.status_code == 200 else None
        if chain in GOPLUS_CHAIN:
            return _creator_pct_goplus(_goplus_token(chain, token))
    except Exception:  # network / malformed answer: the caller treats None as "no information"
        return None
    return None


def _rugcheck(mint: str) -> tuple[list[str], dict]:
    r = _rugcheck_report(mint)
    if r.status_code != 200:
        return [f"rugcheck indisponible ({r.status_code})"], {}
    d = r.json()
    bad = []
    if d.get("mintAuthority") or d.get("freezeAuthority"):
        bad.append("mint/freeze authority non revoquee")
    if d.get("rugged"):
        bad.append("marque rugged")
    # top-10 concentration excludes pools, lockers and burns, but NEVER the creator (calibration 2026-11-05:
    # every knownAccount was excluded before, the creator's wallet included -> concentration understated)
    pools = {a for a, v in (d.get("knownAccounts") or {}).items()
             if str((v or {}).get("type", "")).upper() in ("AMM", "LOCKER", "PROGRAM", "BURN")}
    pools.add("1nc1nerator11111111111111111111111111111111")
    for m in d.get("markets") or []:
        pools |= {v for k in ("pubkey", "liquidityA", "liquidityB", "liquidityAAccount", "liquidityBAccount")
                  if isinstance(v := m.get(k), str)}
    holders = [h for h in d.get("topHolders") or [] if h.get("address") not in pools and h.get("owner") not in pools]
    top = sum(float(h.get("pct", 0)) for h in holders[:10]) / 100
    if top > MAX_TOP10:
        bad.append(f"top 10 detenteurs {top:.0%} > {MAX_TOP10:.0%}")
    lp = max((float((m.get("lp") or {}).get("lpLockedPct") or 0) for m in d.get("markets") or []), default=0) / 100
    if lp < MIN_LP_LOCK:
        bad.append(f"LP verrouillee {lp:.0%} < {MIN_LP_LOCK:.0%}")
    fee = float((d.get("transferFee") or {}).get("pct") or 0) / 100
    if fee >= MAX_TAX:
        bad.append(f"taxe de transfert {fee:.0%}")
    if any(str(x.get("level", "")).lower() == "danger" for x in d.get("risks") or []):
        bad.append("risque 'danger' signale par RugCheck")
    return bad, {"creator_pct": _creator_pct_rugcheck(d)}


def _goplus(chain: str, addr: str) -> tuple[list[str], dict]:
    d = _goplus_token(chain, addr)
    if not d:
        return ["GoPlus sans donnees"], {}
    bad = []
    if d.get("is_honeypot") == "1" or d.get("cannot_sell_all") == "1":
        bad.append("honeypot / revente bloquee (GoPlus)")
    tax = max(float(d.get("buy_tax") or 0), float(d.get("sell_tax") or 0))
    if tax >= MAX_TAX:
        bad.append(f"taxe {tax:.0%}")
    if d.get("is_mintable") == "1":
        bad.append("contrat mintable")
    if d.get("owner_change_balance") == "1":
        bad.append("le proprietaire peut modifier les soldes")
    def _real_holder(h: dict) -> bool:  # exchanges (GoPlus tag), locks, burns and contracts are not owners
        a = str(h.get("address", "")).lower()
        return (h.get("is_contract") != 1 and h.get("is_locked") != 1 and not h.get("tag")
                and not a.startswith(("0x000000000000000000000000000000000000dead", "0x0000000000000000000000000000000000000000")))
    top = sum(float(h.get("percent", 0)) for h in [h for h in (d.get("holders") or []) if _real_holder(h)][:10])
    if top > MAX_TOP10:
        bad.append(f"top 10 detenteurs {top:.0%}")
    locked = sum(float(h.get("percent", 0)) for h in d.get("lp_holders") or [] if h.get("is_locked") == 1
                 or str(h.get("address", "")).lower().startswith("0x000000000000000000000000000000000000dead"))
    if locked < MIN_LP_LOCK:
        bad.append(f"LP verrouillee {locked:.0%}")
    return bad, {"creator_pct": _creator_pct_goplus(d)}


def _honeypot_is(chain: str, addr: str) -> list[str]:
    cid = HONEYPOT_CHAIN.get(chain)
    if not cid:
        return []
    r = requests.get("https://api.honeypot.is/v2/IsHoneypot", params={"address": addr, "chainID": cid}, timeout=20)
    if r.status_code != 200:
        return [f"honeypot.is indisponible ({r.status_code})"]
    d = r.json()
    bad = []
    if (d.get("honeypotResult") or {}).get("isHoneypot"):
        bad.append("honeypot (simulation honeypot.is)")
    if not d.get("simulationSuccess", True):
        bad.append("simulation achat/revente echouee")
    sell_tax = float((d.get("simulationResult") or {}).get("sellTax") or 0) / 100
    if sell_tax >= MAX_TAX:
        bad.append(f"taxe de revente simulee {sell_tax:.0%}")
    return bad


def check(chain: str, token: str, portfolio: str = "court_terme", min_age_h: float | None = None,
          min_liq_usd: float | None = None) -> tuple[bool, list[str], dict]:
    """min_age_h / min_liq_usd: a caller's own thresholds (MemeHunter measures young pairs); the defaults
    (24 h, 100 k$) stay in force for every caller that does not pass them."""
    age_h = MIN_AGE_H if min_age_h is None else min_age_h
    liq_usd = MIN_LIQ_USD if min_liq_usd is None else min_liq_usd
    key = (chain, token.lower(), age_h, liq_usd)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_S:
        return hit[1]
    try:
        reasons, details = _dex(chain, token, age_h, liq_usd)
        if chain == "solana":
            bad, extra = _rugcheck(token)
            reasons, details = reasons + bad, {**details, **extra}
        elif chain in GOPLUS_CHAIN:
            bad, extra = _goplus(chain, token)
            reasons, details = reasons + bad + _honeypot_is(chain, token), {**details, **extra}
        else:
            reasons.append(f"chaine {chain} non couverte par les controles")
    except Exception as e:
        reasons, details = [f"controle impossible ({type(e).__name__})"], {}
    ok = not reasons
    verdict = (ok, reasons, details)
    _cache[key] = (time.time(), verdict)
    log_event("SecurityAgent", portfolio, "APPROVE" if ok else "VETO", details.get("symbol") or token[:10],
              "tous les controles passes" if ok else " ; ".join(reasons)[:280],
              result="autorise" if ok else "refuse", chain=chain, token=token, **{k: v for k, v in details.items() if k != "symbol"})
    return verdict
