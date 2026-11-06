"""One-off paper transfer (2026-11-06): crypto maker scalper cash above its new 5 % share -> HL copy ledger.
Recorded as a deposit so the HL P&L, the daily drawdown and the peak do not count it as a gain. Run with the bot stopped."""
import json, os, sys, time
from pathlib import Path

D = Path(os.environ.get("DATA_DIR") or ".")
sc_p, hl_p = D / "scalp_crypto_copy.json", D / "hl_copy_ledger.json"
sc, hl = json.loads(sc_p.read_text()), json.loads(hl_p.read_text())
keep = 1000.0 * float(os.environ.get("SCALP_SHARE_CRYPTO_COPY", "0.05"))
# open positions keep their own cash (already out of sc['cash']): only the free cash above the share moves
amt = round(sc["cash"] - keep, 2)
if amt <= 0:
    sys.exit(f"nothing to move (cash {sc['cash']:.2f} <= {keep})")
for k in ("cash", "start", "day_start"):
    sc[k] = sc.get(k, 0) - amt
for k in ("cash", "deposits", "daily_start_equity", "tm_peak_equity"):
    hl[k] = hl.get(k, 0) + amt
rec = {"at": time.time(), "usd": amt, "from": "scalp_crypto_copy", "why": "maker scalper 18 % win -> HL copy 68 % win"}
hl.setdefault("transfers", []).append(rec)
sc.setdefault("transfers", []).append({**rec, "usd": -amt, "to": "hl_copy"})
for p, d in ((sc_p, sc), (hl_p, hl)):
    p.with_suffix(".json.bak-transfer").write_text(p.read_text())
    p.write_text(json.dumps(d))
print(f"moved {amt} $ : scalper cash {sc['cash']:.2f}, HL cash {hl['cash']:.2f}, HL deposits {hl['deposits']:.2f}")
