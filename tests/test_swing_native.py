"""Supabase payloads from the swing job must be JSON-serializable (numpy float32 broke run 38482786763)."""

import json
import math
import os
import sys
import unittest
from datetime import date

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.swing.ledger import native


class TestNative(unittest.TestCase):
    def test_numpy_and_dates(self):
        row = {"stop_loss": np.float32(12.5), "n": np.int64(3), "flag": np.bool_(True), "nan": np.float32("nan"),
               "inf": float("inf"), "d": date(2026, 10, 13), "nested": {"x": [np.float64(1.25), np.float32(2.0)]}}
        out = native(row)
        json.dumps(out)                                   # no TypeError
        self.assertEqual(out["stop_loss"], 12.5)
        self.assertIs(type(out["stop_loss"]), float)
        self.assertIs(type(out["n"]), int)
        self.assertIs(out["flag"], True)
        self.assertIsNone(out["nan"])
        self.assertIsNone(out["inf"])
        self.assertEqual(out["d"], "2026-10-13")
        self.assertEqual(out["nested"]["x"], [1.25, 2.0])
        self.assertFalse(any(isinstance(v, float) and not math.isfinite(v) for v in out.values()))


if __name__ == "__main__":
    unittest.main()
