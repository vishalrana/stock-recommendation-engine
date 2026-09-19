"""
test_validator_serialization.py
Tests JSON serialization and outcome evaluation of jobs/validate_ranking.py
"""

import json
import unittest
from unittest.mock import patch
import pandas as pd
import datetime as dt

import os
import sys
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# 1. Verify import succeeds
import jobs.validate_ranking as vr


class TestValidatorSerialization(unittest.TestCase):

    def setUp(self):
        self.sample_row = {
            'id': 101,
            'ticker': 'TEST',
            'scan_date': '2026-02-01',
            'entry_price': 100.0,
            'stop_loss': 95.0,
            'target_1': 110.0,
            'target_2': 120.0,
            'target_3': 130.0,
        }

    def _create_mock_df(self, rows):
        """Helper to create mock daily OHLCV dataframe with DatetimeIndex."""
        dates = pd.date_range(start='2026-02-02', periods=len(rows), freq='B')
        df = pd.DataFrame(rows, index=dates)
        return df

    @patch('jobs.validate_ranking.yf.download')
    def test_stopped_outcome_serialization(self, mock_download):
        """Test stopped outcome serialization."""
        mock_download.return_value = self._create_mock_df([
            {'High': 102.0, 'Low': 94.0, 'Close': 96.0}
        ])
        res = vr.evaluate_signal(self.sample_row)
        self.assertIsNotNone(res)
        self.assertEqual(res['outcome'], 'stopped')
        self.assertIsInstance(res['outcome_date'], str)
        self.assertEqual(res['outcome_date'], '2026-02-02')
        # Test JSON serialization
        serialized = json.dumps(res)
        self.assertIn('"outcome": "stopped"', serialized)
        self.assertIn('"outcome_date": "2026-02-02"', serialized)

    @patch('jobs.validate_ranking.yf.download')
    def test_hit_t1_outcome_serialization(self, mock_download):
        """Test hit_t1 outcome serialization."""
        mock_download.return_value = self._create_mock_df([
            {'High': 112.0, 'Low': 98.0, 'Close': 108.0}
        ])
        res = vr.evaluate_signal(self.sample_row)
        self.assertIsNotNone(res)
        self.assertEqual(res['outcome'], 'hit_t1')
        self.assertIsInstance(res['outcome_date'], str)
        self.assertEqual(res['outcome_date'], '2026-02-02')
        serialized = json.dumps(res)
        self.assertIn('"outcome": "hit_t1"', serialized)
        self.assertIn('"outcome_date": "2026-02-02"', serialized)

    @patch('jobs.validate_ranking.yf.download')
    def test_hit_t2_outcome_serialization(self, mock_download):
        """Test hit_t2 outcome serialization."""
        mock_download.return_value = self._create_mock_df([
            {'High': 122.0, 'Low': 98.0, 'Close': 118.0}
        ])
        res = vr.evaluate_signal(self.sample_row)
        self.assertIsNotNone(res)
        self.assertEqual(res['outcome'], 'hit_t2')
        self.assertIsInstance(res['outcome_date'], str)
        self.assertEqual(res['outcome_date'], '2026-02-02')
        serialized = json.dumps(res)
        self.assertIn('"outcome": "hit_t2"', serialized)
        self.assertIn('"outcome_date": "2026-02-02"', serialized)

    @patch('jobs.validate_ranking.yf.download')
    def test_hit_t3_outcome_serialization(self, mock_download):
        """Test hit_t3 outcome serialization."""
        mock_download.return_value = self._create_mock_df([
            {'High': 135.0, 'Low': 98.0, 'Close': 132.0}
        ])
        res = vr.evaluate_signal(self.sample_row)
        self.assertIsNotNone(res)
        self.assertEqual(res['outcome'], 'hit_t3')
        self.assertIsInstance(res['outcome_date'], str)
        self.assertEqual(res['outcome_date'], '2026-02-02')
        serialized = json.dumps(res)
        self.assertIn('"outcome": "hit_t3"', serialized)
        self.assertIn('"outcome_date": "2026-02-02"', serialized)

    @patch('jobs.validate_ranking.yf.download')
    def test_expired_outcome_serialization(self, mock_download):
        """Test expired outcome serialization (20 bars with no stop/target hit)."""
        rows = [{'High': 105.0, 'Low': 96.0, 'Close': 102.0}] * 20
        mock_download.return_value = self._create_mock_df(rows)
        res = vr.evaluate_signal(self.sample_row)
        self.assertIsNotNone(res)
        self.assertEqual(res['outcome'], 'expired')
        self.assertEqual(res['outcome_holding_days'], 20)
        self.assertIsInstance(res['outcome_date'], str)
        serialized = json.dumps(res)
        self.assertIn('"outcome": "expired"', serialized)
        self.assertIn('"outcome_holding_days": 20', serialized)

    @patch('jobs.validate_ranking.yf.download')
    def test_all_types_are_json_primitives(self, mock_download):
        """Ensure all fields are strictly JSON primitives: str, int, float, bool, or None."""
        mock_download.return_value = self._create_mock_df([
            {'High': 102.0, 'Low': 94.0, 'Close': 96.0}
        ])
        res = vr.evaluate_signal(self.sample_row)
        for k, v in res.items():
            self.assertTrue(
                isinstance(v, (str, int, float, bool, type(None))),
                f"Key {k} has non-primitive type: {type(v)}"
            )
            # Ensure not a date or datetime
            self.assertFalse(isinstance(v, (dt.date, dt.datetime, pd.Timestamp)))


if __name__ == '__main__':
    unittest.main()
