"""Indicateurs techniques, patterns de marché et scan de marché."""
import time
import pandas as pd
import numpy as np
from logging_config import logger
#  INDICATEURS TECHNIQUES
def compute_indicators(closes: pd.Series) -> dict:
    if len(closes) < 27:
        return {}
    try:
        delta = closes.diff()
        gain  = delta.clip(lower=0)
        loss  = (-delta).clip(lower=0)
        rs    = (gain.ewm(com=13,adjust=False).mean() /
                 loss.ewm(com=13,adjust=False).mean().replace(0,np.nan))
        rsi   = float((100-100/(1+rs)).iloc[-1])
        ema9  = float(closes.ewm(span=9, adjust=False).mean().iloc[-1])
        ema20 = float(closes.ewm(span=20,adjust=False).mean().iloc[-1])
        ema50 = float(closes.ewm(span=50,adjust=False).mean().iloc[-1])
        macd_line   = closes.ewm(span=12,adjust=False).mean() - closes.ewm(span=26,adjust=False).mean()
        macd_signal = macd_line.ewm(span=9,adjust=False).mean()
        macd_l = float(macd_line.iloc[-1])
        macd_s = float(macd_signal.iloc[-1])
        macd_h = round(macd_l-macd_s, 6)
        sma20  = closes.rolling(20).mean()
        std20  = closes.rolling(20).std()
        bb_up  = float((sma20+2*std20).iloc[-1])
        bb_lo  = float((sma20-2*std20).iloc[-1])
        bb_pct = round((float(closes.iloc[-1])-bb_lo)/(bb_up-bb_lo)*100,1) if bb_up!=bb_lo else 50.0
        mom5   = float((closes.iloc[-1]-closes.iloc[-6])/closes.iloc[-6]*100)  if len(closes)>=6  else 0.0
        mom15  = float((closes.iloc[-1]-closes.iloc[-16])/closes.iloc[-16]*100) if len(closes)>=16 else 0.0
        vol    = float(closes.pct_change().dropna().iloc[-10:].std()*100) if len(closes)>=10 else 0.0
        return {
            "rsi":round(rsi,1),"ema9":round(ema9,6),"ema20":round(ema20,6),
            "ema50":round(ema50,6),"macd_h":macd_h,"bb_pct":bb_pct,
            "mom5":round(mom5,3),"mom15":round(mom15,3),"vol":round(vol,3),
            "trend":"↑" if ema20>ema50 else "↓",
            "ema_cross":"BULL" if ema9>ema20 else "BEAR",
            "price":float(closes.iloc[-1])
        }
    except Exception as e:
        print(f"[IND] {e}")
        return {}

def get_multi_tf(symbol: str) -> dict:
    result = {}
    for interval, label in [("1","1m"),("5","5m"),("15","15m")]:
        closes = get_klines(symbol, interval, 80)
        if not closes.empty:
            ind = compute_indicators(closes)
            if ind:
                result[label] = ind
    return result

def tf_score(mtf: dict) -> dict:
    score = 0; sigs = []
    for tf, ind in mtf.items():
        rsi   = ind.get("rsi",50)
        macd  = ind.get("macd_h",0)
        mom5  = ind.get("mom5",0)
        cross = ind.get("ema_cross","BEAR")
        if rsi < 32:   score += 2; sigs.append(f"{tf}:RSI_survente")
        elif rsi < 45: score += 1
        elif rsi > 68: score -= 2; sigs.append(f"{tf}:RSI_surachat")
        elif rsi > 55: score -= 1
        if macd > 0:  score += 1; sigs.append(f"{tf}:MACD↑")
        else:         score -= 1
        if mom5 > 0.5:  score += 1
        elif mom5 < -0.5: score -= 1
        if cross == "BULL": score += 1; sigs.append(f"{tf}:EMA_bull")
        else:               score -= 1
    direction = "LONG" if score>=4 else "SHORT" if score<=-4 else "NEUTRE"
    return {"score":score,"direction":direction,"signals":sigs[:6]}

def detect_patterns(symbol: str, ind: dict, vols: list) -> list:
    patterns = []
    try:
        rsi      = ind.get("rsi",50)
        mom5     = ind.get("mom5",0)
        mom15    = ind.get("mom15",0)
        bb_pct   = ind.get("bb_pct",50)
        macd_h   = ind.get("macd_h",0)
        ema_cross= ind.get("ema_cross","BEAR")
        avg_vol   = sum(vols[:-1])/max(len(vols)-1,1) if vols else 0
        last_vol  = vols[-1] if vols else 0
        vol_ratio = last_vol/avg_vol if avg_vol > 0 else 1
        if rsi<28 and bb_pct<5:
            patterns.append({"name":"Survente extrême","signal":"BUY","strength":"fort","score":3})
        elif rsi>72 and bb_pct>95:
            patterns.append({"name":"Surachat extrême","signal":"SELL","strength":"fort","score":3})
        if mom5>1.2 and vol_ratio>2.0 and macd_h>0:
            patterns.append({"name":"Breakout haussier","signal":"BUY","strength":"fort","score":3})
        elif mom5<-1.2 and vol_ratio>2.0 and macd_h<0:
            patterns.append({"name":"Breakdown baissier","signal":"SELL","strength":"fort","score":3})
        if ema_cross=="BULL" and macd_h>0 and rsi<60:
            patterns.append({"name":"EMA Cross Bull","signal":"BUY","strength":"modéré","score":2})
        elif ema_cross=="BEAR" and macd_h<0 and rsi>40:
            patterns.append({"name":"EMA Cross Bear","signal":"SELL","strength":"modéré","score":2})
        if mom5>0.8 and mom15>2.0:
            patterns.append({"name":"Momentum haussier","signal":"BUY","strength":"modéré","score":2})
        elif mom5<-0.8 and mom15<-2.0:
            patterns.append({"name":"Momentum baissier","signal":"SELL","strength":"modéré","score":2})
        if abs(mom5)>4 and vol_ratio>5:
            patterns.append({"name":"⚠️ Pump/Dump","signal":"HOLD","strength":"ALERTE"})
    except Exception as e:
        print(f"[PAT] {e}")
    return patterns

def scan_market() -> list:
    opps = []
    prices = get_prices_batch()
    for symbol in get_scan_symbols():
        price = prices.get(symbol, 0)
        if not price: continue
        closes = get_klines_5m_cached(symbol)
        if len(closes) < 27: continue
        ind = compute_indicators(closes)
        if not ind: continue
        vols = get_volume_data(symbol, "5", 15)
        pats = detect_patterns(symbol, ind, vols)
        score = 0
        if ind["rsi"] < 35: score += 3
        elif ind["rsi"] < 45: score += 1
        if ind["rsi"] > 70: score -= 3
        elif ind["rsi"] > 60: score -= 1
        if ind["macd_h"] > 0: score += 2
        else: score -= 1
        if ind["mom5"] > 1: score += 2
        elif ind["mom5"] < -1: score -= 2
        if ind["ema_cross"] == "BULL": score += 1
        else: score -= 1
        score += get_symbol_confidence_bonus(symbol) // 5

        opps.append({
            "market": "crypto",
            "symbol": symbol,
            "price": price,
            "score": score,
            "direction": "BUY" if score > 0 else "SELL",
            "ind": ind,
            "patterns": pats,
            "has_alert": any(p["signal"] == "HOLD" for p in pats)
        })
    opps.sort(key=lambda x: abs(x["score"]), reverse=True)
    return opps[:15]


