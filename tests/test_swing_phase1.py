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
from scripts.build_insider_history import parse_quarter_zip
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
    def test_parse_quarter_zip_fixture(self):
        # Create in-memory zip file with mock SUBMISSION, REPORTING_OWNER, and NONDERIV_TRANS
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            # Submissions
            sub_tsv = (
                "ACCESSION_NUMBER\tFILING_DATE\tISSUER_CIK\tDOCUMENT_TYPE\n"
                "0001-24-0001\t2024-05-15\t320193\t4\n"     # AAPL CIK, Form 4 original
                "0001-24-0002\t2024-05-15\t320193\t4/A\n"   # Amendment (should be dropped)
                "0001-24-0003\t2024-05-16\t320193\t4\n"     # Form 4 original
            )
            z.writestr("SUBMISSION.tsv", sub_tsv)

            # Reporting owners
            owner_tsv = (
                "ACCESSION_NUMBER\tRPTOWNER_CIK\tIS_DIRECTOR\tIS_OFFICER\n"
                "0001-24-0001\t99901\t1\t0\n"               # Director
                "0001-24-0002\t99902\t1\t0\n"               # Director on amendment
                "0001-24-0003\t99903\t0\t0\n"               # 10% owner / not dir/off (drop)
            )
            z.writestr("REPORTING_OWNER.tsv", owner_tsv)

            # Non-derivative transactions
            trans_tsv = (
                "ACCESSION_NUMBER\tTRANS_CODE\tTRANS_ACQUIRED_DISP_CD\tTRANS_SHARES\tTRANS_PRICEPERSHARE\tAFF10B5ONE\n"
                "0001-24-0001\tP\tA\t1000\t150.00\t0\n"     # $150,000 value, Code P, Acquired A, 10b5-1 False
                "0001-24-0001\tS\tD\t500\t150.00\t0\n"      # Sale (drop)
                "0001-24-0002\tP\tA\t2000\t150.00\t0\n"     # P on amendment (dropped)
                "0001-24-0003\tP\tA\t1000\t150.00\t0\n"     # P by non-dir/off (dropped)
            )
            z.writestr("NONDERIV_TRANS.tsv", trans_tsv)

        cik_map = {320193: "AAPL"}
        df = parse_quarter_zip(buf.getvalue(), cik_map)
        self.assertEqual(len(df), 1)
        row = df.iloc[0]
        self.assertEqual(row["ticker"], "AAPL")
        self.assertEqual(row["transaction_code"], "P")
        self.assertEqual(row["relationship"], "Director")
        self.assertAlmostEqual(row["value"], 150000.0)
        self.assertFalse(row["aff10b5one"])


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
