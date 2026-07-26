"""AI engine — pool de providers, cache et fonctions ask_ai."""
import os, time, hashlib, json, re, random
import requests
from logging_config import logger

# ── Constantes modèles AI ─────────────────────────────────────────────────────
GROQ_FAST_MODEL  = os.environ.get("GROQ_FAST_MODEL",  "llama-3.1-8b-instant")
GROQ_SMART_MODEL = os.environ.get("GROQ_SMART_MODEL", "llama-3.3-70b-versatile")
GROQ_CODE_MODEL  = os.environ.get("GROQ_CODE_MODEL",  "deepseek-r1-distill-llama-70b")
GROQ_KEY         = os.environ.get("GROQ_API_KEY") or os.environ.get("GROQ_KEY", "")
HF_KEY           = os.environ.get("HF_KEY", "")

# ── Verification layer (independent second opinion before committing capital) ─
# Free-tier LLM signals are increasingly arbitraged away when used alone (2026
# "alpha decay" — most funds now run similar GenAI signal generation). Ensemble/
# consensus approaches consistently outperform single-model signals in recent
# benchmarks, so a higher-quality model sanity-checks each trade before it opens.
ANTHROPIC_KEY             = os.environ.get("ANTHROPIC_KEY", "")
CLAUDE_VERIFY_MODEL       = os.environ.get("CLAUDE_VERIFY_MODEL", "claude-haiku-4-5-20251001")
VERIFY_TRADES_WITH_CLAUDE = os.environ.get("VERIFY_TRADES_WITH_CLAUDE", "true").lower() in ("true", "1", "yes")


def verify_trade_with_claude(analysis: dict) -> tuple[bool, str]:
    """Independent coherence check on a trade candidate before capital is committed.

    This does NOT re-analyze the market independently (no live price/indicator feed
    is sent) — it checks whether the primary signal's own stated reasoning is
    internally coherent, not an obviously generic/contradictory justification.
    Fails OPEN (approves) on any error or missing key, so a Claude API hiccup
    never halts trading — this is a precision gate, not the primary decision engine.
    """
    if not VERIFY_TRADES_WITH_CLAUDE or not ANTHROPIC_KEY:
        return True, "verification_disabled"

    patterns = [p.get("name") for p in analysis.get("patterns", []) if isinstance(p, dict)]
    prompt = (
        f"A trading bot wants to open a {analysis.get('signal')} position on "
        f"{analysis.get('symbol')} ({analysis.get('market', 'SPOT')}) at price "
        f"{analysis.get('price')}.\n"
        f"Stated confidence: {analysis.get('confidence')}%\n"
        f"Stated reason: {str(analysis.get('reason', ''))[:300]}\n"
        f"Patterns detected: {patterns}\n\n"
        "You are a skeptical second reviewer, not the primary strategy — you don't have live "
        "market data, only this stated reasoning. Note: this strategy deliberately combines a "
        "trend indicator (EMA cross) with reversal-timing indicators (RSI, Bollinger) — a trade "
        "listing an overbought/oversold RSI alongside an established EMA trend is a normal "
        "mean-reversion-within-trend setup, NOT a contradiction by itself. Veto only if the "
        "reasoning is generic boilerplate with no real signal, mathematically inconsistent (e.g. "
        "cites an oversold RSI to justify a SELL), or the confidence is wildly out of line with "
        "how weak/borderline the cited numbers are. Answer with a first line of exactly APPROVE "
        "or VETO, then a short reason on the next line."
    )

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": CLAUDE_VERIFY_MODEL,
                "max_tokens": 150,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=8,
        )
        resp.raise_for_status()
        blocks = resp.json().get("content", [])
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
        lines = text.splitlines()
        verdict = lines[0].strip().upper() if lines else "APPROVE"
        reason = " ".join(lines[1:]).strip() if len(lines) > 1 else ""
        return ("VETO" not in verdict), reason
    except Exception as e:
        logger.warning(f"[CLAUDE-VERIFY] Echec verification, trade autorise par defaut: {e}")
        return True, f"verification_error: {e}"

HF_MODELS = [
    "Qwen/Qwen2.5-72B-Instruct",
    "mistralai/Mistral-7B-Instruct-v0.3",
    "meta-llama/Llama-3.1-8B-Instruct",
]

#  AI POOL
AI_PROVIDERS = [
    # Groq gratuit: 30 req/min, 14400 req/jour sur llama-3.1-8b-instant
    {"name":"groq_fast",  "calls":0, "window_start":time.time(), "last_call":0,
     "max_calls_per_hour":200, "cooldown":3,  "available":True, "failures":0, "model":GROQ_FAST_MODEL},
    # Groq 70b: 30 req/min, 1000 req/jour (réservé aux décisions importantes)
    {"name":"groq_smart", "calls":0, "window_start":time.time(), "last_call":0,
     "max_calls_per_hour":30,  "cooldown":5,  "available":True, "failures":0, "model":GROQ_SMART_MODEL},
    # HuggingFace gratuit: ~1000 req/jour
    {"name":"huggingface","calls":0, "window_start":time.time(), "last_call":0,
     "max_calls_per_hour":50,  "cooldown":5,  "available":True, "failures":0, "model":None},
    # DeepSeek R1: spécialisé code/raisonnement — parfait pour AEGIS édition code
    {"name":"groq_code",  "calls":0, "window_start":time.time(), "last_call":0,
     "max_calls_per_hour":100, "cooldown":3,  "available":True, "failures":0, "model":GROQ_CODE_MODEL},
]
_pool_stats = {
    "total_calls":0,"calls_by_provider":{},"fallbacks":0,"last_provider":"groq_fast",
    "cache_hits":0,"daily_date":"","groq_fast_daily":0,"groq_smart_daily":0,"hf_daily":0,
}
HF_MODELS = [
    "Qwen/Qwen2.5-72B-Instruct",           # Meilleur modèle HF gratuit
    "mistralai/Mistral-7B-Instruct-v0.3",  # Rapide et fiable
    "meta-llama/Llama-3.1-8B-Instruct",    # Backup Llama
]
_hf_model_idx = 0
_ai_cache: dict = {}
_AI_CACHE_TTL = 60

def _ai_cache_key(prompt: str) -> str:
    return hashlib.md5(prompt[:200].encode()).hexdigest()

def _get_cached_ai(prompt: str) -> dict | None:
    key = _ai_cache_key(prompt)
    if key in _ai_cache:
        ts, result = _ai_cache[key]
        if time.time() - ts < _AI_CACHE_TTL:
            _pool_stats["cache_hits"] += 1
            return result
    return None

def _set_cached_ai(prompt: str, result: dict):
    key = _ai_cache_key(prompt)
    _ai_cache[key] = (time.time(), result)
    if len(_ai_cache) > 200:
        cutoff = time.time() - _AI_CACHE_TTL * 2
        for k in [k for k, (ts, _) in list(_ai_cache.items()) if ts < cutoff]:
            del _ai_cache[k]

def _compress_prompt(prompt: str) -> str:
    lines = prompt.strip().split('\n')
    compressed = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith("Traders:") and len(line) > 100:
            line = line[:100]
        if line.startswith("Gains:") or line.startswith("Erreurs:"):
            line = line[:80]
        compressed.append(line)
    return '\n'.join(compressed)[:450]

def _get_available_provider() -> dict | None:
    now = time.time()
    hour_utc = datetime.now(timezone.utc).hour
    is_night  = hour_utc in NIGHT_HOURS_UTC
    for p in AI_PROVIDERS:
        if not p["available"]:
            if now - p.get("disabled_at", 0) > 1800:
                p["available"] = True
                p["failures"] = 0
            else:
                continue
        if now - p["window_start"] > 3600:
            p["calls"] = 0
            p["window_start"] = now
        limit = p["max_calls_per_hour"] // 2 if is_night else p["max_calls_per_hour"]
        if p["calls"] >= limit:
            continue
        if now - p["last_call"] < p["cooldown"]:
            continue
        return p
    return None

def _call_groq(prompt: str, model: str = None) -> dict:
    r = groq_client.chat.completions.create(
        model=(model or GROQ_FAST_MODEL),
        max_tokens=120,
        temperature=0.1,
        messages=[
            {
                "role": "system",
                "content": (
                    'Réponds UNIQUEMENT avec un objet JSON valide, sans texte autour. '
                    'Format exact: {"signal":"BUY|SELL|HOLD","confidence":0,"reason":"...","risk":"LOW|MEDIUM|HIGH","market":"SPOT|FUTURES"}'
                )
            },
            {"role": "user", "content": prompt[:500]}
        ],
    )

    text = (r.choices[0].message.content or "").strip()
    text = text.replace("```json", "").replace("```", "").strip()

    s = text.find("{")
    e = text.rfind("}") + 1

    if s >= 0 and e > s:
        text = text[s:e]
    else:
        raise Exception(f"Groq non-JSON response: {text[:200]}")

    try:
        result = json.loads(text)
    except Exception:
        raise Exception(f"Groq invalid JSON: {text[:200]}")

    if result.get("signal") not in ("BUY", "SELL", "HOLD"):
        result["signal"] = "HOLD"

    if "confidence" not in result:
        result["confidence"] = 0
    if "reason" not in result:
        result["reason"] = "missing_reason"
    if "risk" not in result:
        result["risk"] = "HIGH"
    if "market" not in result:
        result["market"] = "SPOT"

    return result

def _call_huggingface(prompt: str) -> dict:
    if not HF_KEY:
        raise Exception("HF_KEY manquante")

    model = HF_MODELS[_hf_model_idx % len(HF_MODELS)]

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": 'Réponds UNIQUEMENT en JSON: {"signal":"BUY|SELL|HOLD","confidence":70,"reason":"...","risk":"LOW|MEDIUM|HIGH","market":"SPOT|FUTURES"}'},
            {"role": "user", "content": prompt[:500]}
        ],
        "temperature": 0.1,
        "max_tokens": 120
    }

    r = requests.post(
        "https://router.huggingface.co/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {HF_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=20,
    )

    if r.status_code != 200:
        raise Exception(f"HF HTTP {r.status_code}: {r.text[:200]}")

    raw = r.json()
    text = raw["choices"][0]["message"]["content"].strip()
    text = text.replace("```json", "").replace("```", "").strip()

    s = text.find("{")
    e = text.rfind("}") + 1
    if s >= 0 and e > s:
        result = json.loads(text[s:e])
        if result.get("signal") not in ("BUY", "SELL", "HOLD"):
            result["signal"] = "HOLD"
        return result

    return {"signal":"HOLD","confidence":0,"reason":"hf_no_json","risk":"HIGH","market":"SPOT"}

def ask_ai(prompt: str) -> dict:
    cached = _get_cached_ai(prompt)
    if cached is not None:
        return cached
    if not check_rate_limit("ask_ai_global", 200, 3600):
        return {"signal":"HOLD","confidence":0,"reason":"rate_limit_global","risk":"HIGH"}
    compressed = _compress_prompt(prompt)
    _pool_stats["total_calls"] += 1
    for _ in range(len(AI_PROVIDERS)+1):
        provider = _get_available_provider()
        if provider is None:
            return {"signal":"HOLD","confidence":0,"reason":"pool_epuise","risk":"HIGH"}
        name = provider["name"]
        try:
            provider["calls"] += 1
            provider["last_call"] = time.time()
            result = _call_groq(compressed, provider.get("model")) if name.startswith("groq") else _call_huggingface(compressed)
            provider["failures"] = max(0, provider["failures"]-1)
            _pool_stats["last_provider"] = name
            _pool_stats["calls_by_provider"][name] = \
                _pool_stats["calls_by_provider"].get(name, 0)+1
            _set_cached_ai(prompt, result)
            print(f"[AI] {name} ✅ {result.get('signal')} {result.get('confidence')}%")
            return result
        except Exception as e:
            err = str(e)
            provider["failures"] += 1
            print(f"[AI] {name} err: {err[:50]}")
            if "rate_limit" in err or "429" in err:
                provider["calls"] = provider["max_calls_per_hour"]
            if provider["failures"] >= 3:
                provider["available"] = False
                provider["disabled_at"] = time.time()
            _pool_stats["fallbacks"] += 1
    return {"signal":"HOLD","confidence":0,"reason":"all_failed","risk":"HIGH"}

def vote(prompt: str) -> dict:
    r1 = ask_ai(prompt)
    if r1.get("reason") in ("pool_epuise","all_failed","rate_limit_global"):
        return {**r1,"votes":[r1["signal"]],"consensus":"0/1"}
    if r1["signal"] == "HOLD" or r1.get("confidence",0) < 60:
        return {**r1,"votes":[r1["signal"]],"consensus":"1/1"}
    r2 = ask_ai(prompt)
    if r2["signal"] == r1["signal"]:
        conf = min(95, round((r1.get("confidence",0)+r2.get("confidence",0))/2)+5)
        return {"signal":r1["signal"],"confidence":conf,
                "reason":r2.get("reason",r1.get("reason","")),
                "risk":r1.get("risk","MEDIUM"),"market":r1.get("market","SPOT"),
                "votes":[r1["signal"],r2["signal"]],"consensus":"2/2"}
    return {"signal":"HOLD","confidence":0,
            "reason":f"Désaccord ({r1['signal']}/{r2['signal']})",
            "risk":"HIGH","votes":[r1["signal"],r2["signal"]],"consensus":"0/2"}

def _can_call_ai() -> bool:
    return _get_available_provider() is not None

def ask_model_single(prompt: str, model: str=None) -> dict:
    return ask_ai(prompt)

def get_pool_status() -> str:
    now = time.time()
    lines = ["🧠 AI POOL STATUS\n━━━━━━━━━━━━━"]
    for p in AI_PROVIDERS:
        cd = max(0, int(p["cooldown"]-(now-p["last_call"])))
        st = "✅" if p["available"] and p["calls"] < p["max_calls_per_hour"] else "⏸"
        total = _pool_stats["calls_by_provider"].get(p["name"], 0)
        lines.append(f"  {st} {p['name']:12s} {p['calls']}/{p['max_calls_per_hour']}/h "
                     f"({total} total) cd:{cd}s")
    lines.append(f"  Total: {_pool_stats['total_calls']} | Fallbacks: {_pool_stats['fallbacks']}")
    lines.append(f"  Cache hits: {_pool_stats['cache_hits']} | Dernier: {_pool_stats['last_provider']}")
    return "\n".join(lines)

