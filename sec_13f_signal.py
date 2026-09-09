"""
sec_13f_signal.py -- Standalone, isolated 13F institutional-holdings
conviction signal (SEC Form 13F-HR quarterly equity disclosures).

STATUS: NOT wired into bot.py or the live trading loop. Same isolation
contract as strategy_ledger.py / congress_trades_signal.py -- zero imports
from bot.py/agents/*, no import-time side effects, no network calls unless
a fetch function is actually called.

WHAT THIS IS (and isn't): 13F is a MANDATORY disclosure for every investment
manager with >$100M in US equity AUM -- it is NOT a performance leaderboard
the way Hyperliquid's is, and there is no public ranking of 13F filers by
ROI. "Conviction" here means "did a manager we deliberately chose for a
long, real, independently-documented track record (not social-media
reputation) meaningfully change a position between two consecutive
quarterly filings" -- a fundamentally weaker and slower kind of signal than
the Hyperliquid sleeve, closer in spirit to the Solana single-wallet filter
than to top-N-by-ROI copy trading. CURATED_FILERS below is a manually
chosen, editable dict -- any addition should meet the same bar as
SOLANA_SMART_WALLETS in bot.py: a real, long, independently verifiable
public track record, never "looks good lately" or "trending on social
media". All CIKs below were looked up and confirmed live against SEC EDGAR
on 2026-09-08 (each returns real, current 13F-HR filings under that name).

REPORTING LAG IS STRUCTURAL AND SEVERE: SEC's deadline is 45 days after
quarter-end (confirmed unchanged for 2026: filings due ~Feb 17 / May 15 /
Aug 14 / Nov 16 -- verified live against Berkshire Hathaway's actual filing
dates), and large managers routinely file right at the deadline. Any
position this module surfaces reflects the manager's book as of *last
quarter's last day* -- at best 45 days stale the day it's filed, and up to
~135 days stale right before the *next* quarter's filing lands. This is a
multi-month, medium-term conviction signal only -- never treat it as
anything close to real-time, and never use it alone to size a trade (a
13F position can be fully unwound the day after quarter-end and this
module would have no way to know for another ~3 months).

ACCESS (researched + verified live 2026-09-08):
  GET https://data.sec.gov/submissions/CIK{10-digit-zero-padded}.json
      -> filing history incl. every 13F-HR accession number + filing date
         (free, no API key)
  GET https://www.sec.gov/Archives/edgar/data/{cik}/{accession-no-dashes}/index.json
      -> lists the filing's files; the holdings ("information table") is
         whichever .xml file in that list is NOT "primary_doc.xml"
  GET https://www.sec.gov/Archives/edgar/data/{cik}/{accession-no-dashes}/{file}
      -> the actual <informationTable> XML: nameOfIssuer/cusip/value/shares
         per <infoTable> row. IMPORTANT, verified live against Berkshire's
         2026-08-14 filing: the SAME issuer can appear as multiple separate
         <infoTable> rows within one accession (split across
         otherManager-linked sub-filers) -- holdings MUST be summed by cusip,
         never read as "one row per issuer".

SEC requires every automated request identify a real contact (their written
API policy, not optional) via the User-Agent header. Set the SEC_EDGAR_CONTACT
env var (e.g. "you@example.com") before real use -- a generic placeholder
default is provided so a quick manual test doesn't crash, but SEC may
throttle or block sustained use of a non-identifying placeholder. Never
hardcode a personal email directly in source -- env var only, same
convention as every other credential-shaped value in this repo (see
ETHERSCAN_API_KEY etc. in agents/wallet_copier_agent.py).
Rate limit: SEC asks for <=10 requests/sec; this module sleeps
SEC_MIN_REQUEST_INTERVAL_SEC between calls by default, well under that.

USAGE:
    from sec_13f_signal import CURATED_FILERS, get_last_two_filings, diff_holdings
    filings = get_last_two_filings(CURATED_FILERS["Berkshire Hathaway"])
    changes = diff_holdings(filings[1]["holdings"], filings[0]["holdings"])
    for c in changes[:10]:
        print(c)
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
import xml.etree.ElementTree as ET
from typing import Optional

# Verified live against SEC EDGAR 2026-09-08 (each CIK returns current,
# real 13F-HR filings under this name at time of verification).
CURATED_FILERS: dict[str, str] = {
    "Berkshire Hathaway": "0001067983",       # Warren Buffett
    "Scion Asset Management": "0001649339",   # Michael Burry
    "Bridgewater Associates": "0001350694",   # Ray Dalio
    "Duquesne Family Office": "0001536411",   # Stanley Druckenmiller
    "Pershing Square Capital": "0001336528",  # Bill Ackman
    "Third Point": "0001040273",              # Dan Loeb
    "Renaissance Technologies": "0001037389", # Jim Simons legacy
}

SEC_EDGAR_CONTACT = os.environ.get("SEC_EDGAR_CONTACT", "trading-bot-research placeholder@example.com")
SEC_MIN_REQUEST_INTERVAL_SEC = float(os.environ.get("SEC_MIN_REQUEST_INTERVAL_SEC", 0.25))  # <=4 req/s, under SEC's 10/s ask
REQUEST_TIMEOUT_SEC = 20

_last_request_ts = 0.0


def _throttle():
    global _last_request_ts
    wait = SEC_MIN_REQUEST_INTERVAL_SEC - (time.time() - _last_request_ts)
    if wait > 0:
        time.sleep(wait)
    _last_request_ts = time.time()


def _get(url: str) -> bytes:
    _throttle()
    req = urllib.request.Request(url, headers={"User-Agent": SEC_EDGAR_CONTACT})
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SEC) as resp:
        return resp.read()


def _get_json(url: str) -> dict:
    return json.loads(_get(url).decode("utf-8"))


def list_13f_accessions(cik: str, limit: int = 2) -> list[dict]:
    """Returns up to `limit` most-recent 13F-HR filings for a CIK, newest
    first, as [{"accession": "0001193125-26-352200", "filing_date": "2026-08-14"}]."""
    cik10 = cik.zfill(10)
    data = _get_json(f"https://data.sec.gov/submissions/CIK{cik10}.json")
    recent = data["filings"]["recent"]
    out = []
    for form, accn, fdate in zip(recent["form"], recent["accessionNumber"], recent["filingDate"]):
        if form.startswith("13F-HR"):
            out.append({"accession": accn, "filing_date": fdate})
            if len(out) >= limit:
                break
    return out


def _find_info_table_filename(cik: str, accession_no_dashes: str) -> Optional[str]:
    idx = _get_json(f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession_no_dashes}/index.json")
    for item in idx["directory"]["item"]:
        name = item["name"]
        if name.endswith(".xml") and name != "primary_doc.xml":
            return name
    return None


def parse_info_table_xml(xml_bytes: bytes) -> list[dict]:
    """Parses a 13F <informationTable> document into a list of
    {cusip, name_of_issuer, value, shares} rows, SUMMED by cusip (a single
    issuer can legitimately appear multiple times per accession -- see
    module docstring)."""
    root = ET.fromstring(xml_bytes)
    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag.split("}")[0] + "}"

    by_cusip: dict[str, dict] = {}
    for info in root.findall(f"{ns}infoTable"):
        cusip = (info.findtext(f"{ns}cusip") or "").strip()
        if not cusip:
            continue
        name = (info.findtext(f"{ns}nameOfIssuer") or "").strip()
        value = float(info.findtext(f"{ns}value") or 0)  # reported in thousands of USD, per SEC form instructions
        shrs_el = info.find(f"{ns}shrsOrPrnAmt")
        shares = float(shrs_el.findtext(f"{ns}sshPrnamt") or 0) if shrs_el is not None else 0.0

        entry = by_cusip.setdefault(cusip, {"cusip": cusip, "name_of_issuer": name, "value": 0.0, "shares": 0.0})
        entry["value"] += value
        entry["shares"] += shares

    return sorted(by_cusip.values(), key=lambda r: r["value"], reverse=True)


def fetch_holdings(cik: str, accession: str) -> list[dict]:
    accn_nodash = accession.replace("-", "")
    filename = _find_info_table_filename(cik, accn_nodash)
    if filename is None:
        return []
    xml_bytes = _get(f"https://www.sec.gov/Archives/edgar/data/{cik}/{accn_nodash}/{filename}")
    return parse_info_table_xml(xml_bytes)


def get_last_two_filings(cik: str) -> list[dict]:
    """Returns up to 2 most-recent 13F-HR filings, newest first, each as
    {"accession", "filing_date", "holdings": [...]}. Fewer than 2 means the
    filer has <2 13F-HR filings on record (new filer) -- caller should
    handle that, diff_holdings() below treats a missing prior filing as
    "everything is new"."""
    accessions = list_13f_accessions(cik, limit=2)
    out = []
    for a in accessions:
        out.append({**a, "holdings": fetch_holdings(cik, a["accession"])})
    return out


def diff_holdings(prev_holdings: list[dict], curr_holdings: list[dict], new_position_min_value: float = 0.0) -> list[dict]:
    """Quarter-over-quarter diff by cusip. Returns a list of
    {cusip, name_of_issuer, action, prev_value, curr_value, pct_change}
    sorted by abs(pct_change) desc (biggest conviction moves first), where
    action is one of NEW / EXIT / INCREASED / DECREASED / UNCHANGED.
    Values are in thousands of USD, per the raw 13F `value` field."""
    prev_by_cusip = {h["cusip"]: h for h in prev_holdings}
    curr_by_cusip = {h["cusip"]: h for h in curr_holdings}
    all_cusips = set(prev_by_cusip) | set(curr_by_cusip)

    rows = []
    for cusip in all_cusips:
        prev = prev_by_cusip.get(cusip)
        curr = curr_by_cusip.get(cusip)
        prev_value = prev["value"] if prev else 0.0
        curr_value = curr["value"] if curr else 0.0
        name = (curr or prev)["name_of_issuer"]

        if prev is None:
            if curr_value < new_position_min_value:
                continue
            action = "NEW"
            pct_change = float("inf")
        elif curr is None:
            action = "EXIT"
            pct_change = -100.0
        else:
            pct_change = ((curr_value - prev_value) / prev_value * 100) if prev_value else float("inf")
            if abs(pct_change) < 1e-9:
                action = "UNCHANGED"
            elif pct_change > 0:
                action = "INCREASED"
            else:
                action = "DECREASED"

        rows.append({
            "cusip": cusip, "name_of_issuer": name, "action": action,
            "prev_value": prev_value, "curr_value": curr_value,
            "pct_change": pct_change,
        })

    rows.sort(key=lambda r: abs(r["pct_change"]) if r["pct_change"] != float("inf") else 1e18, reverse=True)
    return rows


# ---------------------------------------------------------------------- #
# Unit tests (stdlib only, NO network calls -- run with:
#   python sec_13f_signal.py
# ---------------------------------------------------------------------- #
if __name__ == "__main__":
    import unittest

    SAMPLE_XML = b"""<?xml version="1.0"?>
<informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">
  <infoTable>
    <nameOfIssuer>ALLY FINL INC</nameOfIssuer>
    <cusip>02005N100</cusip>
    <value>577211815</value>
    <shrsOrPrnAmt><sshPrnamt>12561737</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt>
  </infoTable>
  <infoTable>
    <nameOfIssuer>ALLY FINL INC</nameOfIssuer>
    <cusip>02005N100</cusip>
    <value>128838056</value>
    <shrsOrPrnAmt><sshPrnamt>2803875</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt>
  </infoTable>
  <infoTable>
    <nameOfIssuer>APPLE INC</nameOfIssuer>
    <cusip>037833100</cusip>
    <value>1000000</value>
    <shrsOrPrnAmt><sshPrnamt>5000</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt>
  </infoTable>
</informationTable>"""

    class TestSec13FSignal(unittest.TestCase):
        def test_curated_filers_are_10digit_ciks(self):
            for name, cik in CURATED_FILERS.items():
                self.assertEqual(len(cik), 10, name)
                self.assertTrue(cik.isdigit(), name)

        def test_parse_info_table_sums_duplicate_issuer_by_cusip(self):
            holdings = parse_info_table_xml(SAMPLE_XML)
            by_cusip = {h["cusip"]: h for h in holdings}
            self.assertAlmostEqual(by_cusip["02005N100"]["value"], 577211815 + 128838056)
            self.assertAlmostEqual(by_cusip["02005N100"]["shares"], 12561737 + 2803875)
            self.assertEqual(by_cusip["037833100"]["value"], 1000000)

        def test_parse_info_table_sorted_by_value_desc(self):
            holdings = parse_info_table_xml(SAMPLE_XML)
            values = [h["value"] for h in holdings]
            self.assertEqual(values, sorted(values, reverse=True))

        def test_diff_new_position(self):
            prev = []
            curr = [{"cusip": "AAA", "name_of_issuer": "NEWCO", "value": 5000, "shares": 100}]
            rows = diff_holdings(prev, curr)
            self.assertEqual(rows[0]["action"], "NEW")
            self.assertEqual(rows[0]["curr_value"], 5000)

        def test_diff_exit_position(self):
            prev = [{"cusip": "AAA", "name_of_issuer": "OLDCO", "value": 5000, "shares": 100}]
            curr = []
            rows = diff_holdings(prev, curr)
            self.assertEqual(rows[0]["action"], "EXIT")
            self.assertEqual(rows[0]["curr_value"], 0)

        def test_diff_increased_and_decreased(self):
            prev = [
                {"cusip": "AAA", "name_of_issuer": "UP", "value": 1000, "shares": 10},
                {"cusip": "BBB", "name_of_issuer": "DOWN", "value": 1000, "shares": 10},
            ]
            curr = [
                {"cusip": "AAA", "name_of_issuer": "UP", "value": 2000, "shares": 20},
                {"cusip": "BBB", "name_of_issuer": "DOWN", "value": 500, "shares": 5},
            ]
            rows = {r["cusip"]: r for r in diff_holdings(prev, curr)}
            self.assertEqual(rows["AAA"]["action"], "INCREASED")
            self.assertAlmostEqual(rows["AAA"]["pct_change"], 100.0)
            self.assertEqual(rows["BBB"]["action"], "DECREASED")
            self.assertAlmostEqual(rows["BBB"]["pct_change"], -50.0)

        def test_diff_unchanged(self):
            prev = [{"cusip": "AAA", "name_of_issuer": "FLAT", "value": 1000, "shares": 10}]
            curr = [{"cusip": "AAA", "name_of_issuer": "FLAT", "value": 1000, "shares": 10}]
            rows = diff_holdings(prev, curr)
            self.assertEqual(rows[0]["action"], "UNCHANGED")

        def test_diff_new_position_min_value_filter(self):
            prev = []
            curr = [{"cusip": "AAA", "name_of_issuer": "TINY", "value": 10, "shares": 1}]
            rows = diff_holdings(prev, curr, new_position_min_value=1000)
            self.assertEqual(rows, [])

        def test_diff_sorted_biggest_move_first(self):
            prev = [
                {"cusip": "AAA", "name_of_issuer": "SMALL_MOVE", "value": 1000, "shares": 10},
                {"cusip": "BBB", "name_of_issuer": "BIG_MOVE", "value": 1000, "shares": 10},
            ]
            curr = [
                {"cusip": "AAA", "name_of_issuer": "SMALL_MOVE", "value": 1050, "shares": 10},
                {"cusip": "BBB", "name_of_issuer": "BIG_MOVE", "value": 5000, "shares": 50},
            ]
            rows = diff_holdings(prev, curr)
            self.assertEqual(rows[0]["cusip"], "BBB")

    unittest.main(verbosity=2)
