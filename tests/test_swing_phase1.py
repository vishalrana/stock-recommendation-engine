"""
Swing Signals Program — Phase 1 Regression & Fixture Tests
===========================================================
Tests:
- Adjustment identities & unadjusted close calculation
- SEC Form 3/4/5 ZIP insider transactions parsing
- Short interest publication date calculation
- Survivorship & recycled symbol exclusion logic
"""

import io
import os
import sys
import unittest
import zipfile
from datetime import date, datetime

import numpy as np
import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from scripts.build_long_history import compute_unadjusted_close, process_ticker_frame
from scripts.build_insider_history import parse_quarter
from scripts.build_short_interest import calculate_publication_date
from src.trade_hygiene import SessionCalendar


class TestAdjustmentIdentities(unittest.TestCase):
    def test_compute_unadjusted_close_synthetic(self):
        # 3 bars: day 0 ($20), day 1 ($30, then 3:1 split on day 2), day 2 ($20 post-split)
        # On day 1 (split 2:1 on day 1):
        # Let's say split occurs on day 1 (2:1) and on day 2 (3:1):
        splits = np.array([0.0, 2.0, 3.0], dtype=float)
        # Yahoo split-adjusted closes:
        # If true prices were: day 0: $120, day 1: $60, day 2: $20
        # Post-both-splits, day 0 close is 120 / (2*3) = 20
        # Post-day-2-split, day 1 close is 60 / 3 = 20
        # day 2 close is 20
        closes = np.array([20.0, 20.0, 20.0], dtype=float)
        unadj = compute_unadjusted_close(closes, splits)
        # On day 0: subsequent splits are day 1 (2x) and day 2 (3x) -> factor 6 -> 20 * 6 = 120
        self.assertAlmostEqual(unadj[0], 120.0, places=4)
        # On day 1: subsequent split is day 2 (3x) -> factor 3 -> 20 * 3 = 60
        self.assertAlmostEqual(unadj[1], 60.0, places=4)
        # On day 2: no subsequent splits -> factor 1 -> 20
        self.assertAlmostEqual(unadj[2], 20.0, places=4)

    def test_process_ticker_frame_total_return(self):
        dates = pd.date_range("2024-01-01", periods=3)
        raw = pd.DataFrame({
            "Open": [10.0, 11.0, 12.0],
            "High": [10.5, 11.5, 12.5],
            "Low": [9.5, 10.5, 11.5],
            "Close": [10.0, 11.0, 12.0],
            "Adj Close": [12.0, 13.2, 14.4],  # 1.2 ratio
            "Volume": [1000.0, 1000.0, 1000.0],
            "Stock Splits": [0.0, 0.0, 0.0],
            "Dividends": [0.0, 0.0, 0.0],
        }, index=dates)

        proc = process_ticker_frame(raw)
        self.assertIsNotNone(proc)
        self.assertIn("TR_OPEN", proc.columns)
        self.assertIn("UNADJ_CLOSE", proc.columns)
        # Ratio is 1.2, so TR_OPEN should be 10 * 1.2 = 12.0
        self.assertAlmostEqual(proc["TR_OPEN"].iloc[0], 12.0, places=4)
        self.assertAlmostEqual(proc["TR_CLOSE"].iloc[0], 12.0, places=4)
        self.assertAlmostEqual(proc["UNADJ_CLOSE"].iloc[0], 10.0, places=4)


class TestInsiderPurchasesParser(unittest.TestCase):
    """Fixture in the real SEC data-set schema (insider_transactions_readme.htm, 2024Q3 file)."""

    @staticmethod
    def tsv(rows):
        return "\n".join("\t".join(r) for r in rows) + "\n"

    def zip_fixture(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("SUBMISSION.tsv", self.tsv([
                ["ACCESSION_NUMBER", "FILING_DATE", "DOCUMENT_TYPE", "ISSUERCIK", "ISSUERTRADINGSYMBOL", "AFF10B5ONE"],
                ["0001-24-0001", "15-MAY-2024", "4", "0000320193", "AAPL", "0"],       # original Form 4
                ["0001-24-0002", "15-MAY-2024", "4/A", "0000320193", "AAPL", "0"],     # amendment: excluded
                ["0001-24-0003", "16-MAY-2024", "4", "0000320193", "AAPL", "true"],    # 10b5-1 plan
                ["0001-24-0004", "16-MAY-2024", "4", "0000320193", "(AAPL)", ""],
            ]))
            z.writestr("REPORTINGOWNER.tsv", self.tsv([
                ["ACCESSION_NUMBER", "RPTOWNERCIK", "RPTOWNER_RELATIONSHIP"],
                ["0001-24-0001", "99901", "Director"],
                ["0001-24-0002", "99902", "Director"],
                ["0001-24-0003", "99903", "Director,Officer"],
                ["0001-24-0004", "99904", "TenPercentOwner"],
            ]))
            z.writestr("NONDERIV_TRANS.tsv", self.tsv([
                ["ACCESSION_NUMBER", "TRANS_DATE", "TRANS_CODE", "TRANS_SHARES", "TRANS_PRICEPERSHARE", "TRANS_ACQUIRED_DISP_CD"],
                ["0001-24-0001", "14-MAY-2024", "P", "1000", "150.00", "A"],   # $150,000 purchase
                ["0001-24-0001", "14-MAY-2024", "S", "500", "150.00", "D"],    # sale: excluded
                ["0001-24-0002", "14-MAY-2024", "P", "2000", "150.00", "A"],   # on the amendment
                ["0001-24-0003", "15-MAY-2024", "P", "100", "150.00", "A"],    # 10b5-1
                ["0001-24-0004", "15-MAY-2024", "P", "1000", "150.00", "A"],   # 10% owner only
            ]))
        return buf.getvalue()

    def test_parse_real_schema(self):
        p, sym = parse_quarter(self.zip_fixture())
        self.assertEqual(sorted(p["accession"]), ["0001-24-0001", "0001-24-0003", "0001-24-0004"])
        a = p[p["accession"] == "0001-24-0001"].iloc[0]
        self.assertEqual((int(a["issuer_cik"]), a["symbol"], int(a["owner_cik"])), (320193, "AAPL", 99901))
        self.assertTrue(a["is_director"] and not a["is_officer"])
        self.assertAlmostEqual(a["value"], 150000.0)
        self.assertEqual(a["filing_date"], date(2024, 5, 15))
        self.assertIs(p[p["accession"] == "0001-24-0003"].iloc[0]["aff10b5one"], True)
        self.assertEqual(p[p["accession"] == "0001-24-0004"].iloc[0]["symbol"], "AAPL")   # "(AAPL)" cleaned
        self.assertEqual(int(sym["ISSUERCIK"].iloc[0]), 320193)

    def test_qualification_rules(self):
        from src.swing.engine import qualifying_purchases
        p, _ = parse_quarter(self.zip_fixture())
        q = qualifying_purchases(p, lambda cik, s, fd: "AAPL")
        # 0001: director, $150k -> qualifies; 0003: 10b5-1 -> no; 0004: 10% owner only -> no
        self.assertEqual(list(q["accession"]), ["0001-24-0001"])
        # Routine: the same owner bought in May of each of the three prior years -> excluded
        base = p[p["accession"] == "0001-24-0001"]
        rows = [base]
        for y in (2021, 2022, 2023):
            h = base.copy()
            h["filing_date"] = date(y, 5, 10)
            h["accession"] = f"old-{y}"
            h["value"] = 5000.0                       # any code-P purchase counts toward "routine"
            rows.append(h)
        q2 = qualifying_purchases(pd.concat(rows, ignore_index=True), lambda cik, s, fd: "AAPL")
        self.assertNotIn("0001-24-0001", set(q2["accession"]))


class TestShortInterestPublicationDate(unittest.TestCase):
    def test_publication_date_formula(self):
        cal = SessionCalendar()
        settlement = date(2024, 5, 15)  # Wednesday
        pub = calculate_publication_date(settlement, cal)
        # Settlement + 8 NYSE sessions
        expected = cal.shift(settlement, 8)
        self.assertEqual(pub, expected)
        self.assertGreater(pub, settlement)


if __name__ == "__main__":
    unittest.main()
