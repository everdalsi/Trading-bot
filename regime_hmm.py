"""RegimeHMM (T4, 06/11): hidden-Markov regime of BTC, MEASUREMENT next to regime.py (MA50/MA200 + vol). numpy only.

Model: 3-state Gaussian HMM on daily BTC log returns (2 years of Binance daily closes), fitted by Baum-Welch (EM).
States are sorted by mean return -> labels risk_off / neutral / risk_on. The current state probability comes from the
forward pass (no look-ahead). Faster to flip than a 200-day average; it only earns weight after the IC monitor shows it
predicts the next days' returns better than regime.py (user rule: measurement first).

    current() -> {label, probs{risk_off,neutral,risk_on}, means_daily_pct, vols_daily_pct, at}
    start(every_s=3600) -> thread writing DATA_DIR/regime_hmm.json
"""
import json
import os
import threading
import time
import urllib.request
from pathlib import Path

import numpy as np

DATA_DIR = Path(os.environ.get("DATA_DIR") or ".")
LABELS = ("risk_off", "neutral", "risk_on")



def _atomic_write(path, text: str) -> None:
    """Write via a temp file + rename so a reader never sees a half-written file (06/11 audit)."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)

def closes(days: int = 730) -> np.ndarray:
    url = f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1d&limit={min(days, 1000)}"
    with urllib.request.urlopen(url, timeout=20) as r:
        k = json.loads(r.read())
    return np.array([float(x[4]) for x in k[:-1]])          # closed days only (no look-ahead on today's candle)


def _gauss(x: np.ndarray, mu: np.ndarray, sd: np.ndarray) -> np.ndarray:
    return np.exp(-0.5 * ((x[:, None] - mu) / sd) ** 2) / (sd * np.sqrt(2 * np.pi)) + 1e-300


def fit(x: np.ndarray, k: int = 3, iters: int = 200, seed: int = 7) -> dict:
    """Baum-Welch with scaling. x = 1-D returns."""
    rng = np.random.default_rng(seed)
    qs = np.quantile(x, np.linspace(0.15, 0.85, k))
    mu, sd = qs + rng.normal(0, 1e-4, k), np.full(k, x.std())
    A = np.full((k, k), 0.05 / (k - 1)) + np.eye(k) * (0.95 - 0.05 / (k - 1))
    pi = np.full(k, 1 / k)
    n, prev = len(x), -np.inf
    for _ in range(iters):
        B = _gauss(x, mu, sd)
        alpha, c = np.zeros((n, k)), np.zeros(n)
        alpha[0] = pi * B[0]; c[0] = alpha[0].sum(); alpha[0] /= c[0]
        for t in range(1, n):
            alpha[t] = (alpha[t - 1] @ A) * B[t]; c[t] = alpha[t].sum(); alpha[t] /= c[t]
        beta = np.ones((n, k))
        for t in range(n - 2, -1, -1):
            beta[t] = (A @ (B[t + 1] * beta[t + 1])) / c[t + 1]
        gamma = alpha * beta
        gamma /= gamma.sum(1, keepdims=True)
        xi = (alpha[:-1, :, None] * A[None] * (B[1:] * beta[1:])[:, None, :]) / c[1:, None, None]
        pi = gamma[0]
        A = xi.sum(0) / xi.sum((0, 2))[:, None]
        w = gamma.sum(0)
        mu = (gamma * x[:, None]).sum(0) / w
        sd = np.sqrt((gamma * (x[:, None] - mu) ** 2).sum(0) / w) + 1e-6
        ll = np.log(c).sum()
        if abs(ll - prev) < 1e-6:
            break
        prev = ll
    order = np.argsort(mu)
    return {"mu": mu[order], "sd": sd[order], "A": A[np.ix_(order, order)], "filtered": alpha[:, order], "ll": float(prev)}


def current() -> dict:
    px = closes()
    x = np.diff(np.log(px))
    m = fit(x)
    p = m["filtered"][-1]
    return {"at": time.time(), "label": LABELS[int(np.argmax(p))],
            "probs": {LABELS[i]: round(float(p[i]), 3) for i in range(3)},
            "means_daily_pct": [round(100 * float(v), 3) for v in m["mu"]],
            "vols_daily_pct": [round(100 * float(v), 2) for v in m["sd"]],
            "stay_prob": [round(float(m["A"][i, i]), 3) for i in range(3)], "days": len(x),
            "mode": "mesure (aucun poids)"}


_thread = None

def start(every_s: int = 3600) -> threading.Thread:
    """Idempotent: bot.py starts agents from auto_start() AND from the Telegram /start command."""
    global _thread
    if _thread is not None and _thread.is_alive():
        return _thread
    def loop():
        while True:
            try:
                _atomic_write(DATA_DIR / "regime_hmm.json", json.dumps(current()))
            except Exception:
                pass
            time.sleep(every_s)
    t = threading.Thread(target=loop, name="RegimeHMM", daemon=True)
    t.start()
    _thread = t
    return t


if __name__ == "__main__":
    print(json.dumps(current(), indent=1))
