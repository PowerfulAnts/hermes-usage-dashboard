"""Focused contract tests for Hermes-only tokens and provider account status."""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "dashboard"))

import sources  # noqa: E402


class HitRateTests(unittest.TestCase):
    def test_cache_hit_rate_uses_full_prompt(self):
        self.assertEqual(
            sources.hit_rate({"input": 600, "cached": 300, "cache_write": 100}),
            30.0,
        )

    def test_used_provider_without_cache_hits_is_zero(self):
        self.assertEqual(sources.hit_rate({"input": 100, "cached": 0, "cache_write": 0}), 0.0)

    def test_period_without_prompt_tokens_is_unknown(self):
        self.assertIsNone(sources.hit_rate({"input": 0, "cached": 0, "cache_write": 0}))

    def test_cache_writes_without_hits_is_real_zero(self):
        self.assertEqual(sources.hit_rate({"input": 600, "cached": 0, "cache_write": 400}), 0.0)


class HermesDatabaseContractTests(unittest.TestCase):
    def test_collect_groups_only_session_model_usage_by_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = os.path.join(directory, "state.db")
            conn = sqlite3.connect(db_path)
            conn.execute("""
                CREATE TABLE session_model_usage (
                    billing_provider TEXT, input_tokens INTEGER,
                    output_tokens INTEGER, cache_read_tokens INTEGER,
                    cache_write_tokens INTEGER, reasoning_tokens INTEGER,
                    api_call_count INTEGER, estimated_cost_usd REAL DEFAULT 0,
                    actual_cost_usd REAL DEFAULT 0, last_seen REAL
                )
            """)
            conn.executemany(
                "INSERT INTO session_model_usage VALUES (?,?,?,?,?,?,?,?,?,?)",
                [
                    ("nous", 600, 100, 300, 100, 20, 2, 0.0, 0.0, time.time()),
                    ("surplusintelligence", 50, 10, 0, 0, 0, 1, 0.0125, 0.02, time.time()),
                    ("openrouter", 50, 10, 0, 0, 0, 1, 0.0, 0.0, time.time()),
                ],
            )
            conn.commit()
            conn.close()

            with patch.object(sources, "_db_path", return_value=db_path):
                result = sources.collect(days=1)

        self.assertEqual(result["meta"]["scope"], "hermes-only")
        # Unknown providers (e.g. surplusintelligence) group automatically.
        self.assertEqual(set(result["providers"]), {"nous", "surplusintelligence", "openrouter"})
        self.assertEqual(result["providers"]["nous"]["hit_rate_pct"], 30.0)
        self.assertEqual(result["providers"]["openrouter"]["hit_rate_pct"], 0.0)
        self.assertEqual(result["providers"]["surplusintelligence"]["cost_usd"], 0.02)


class CostAggregationTests(unittest.TestCase):
    def test_actual_cost_wins_over_estimate(self):
        bucket = sources._bucket()
        sources._bump(bucket, 10, 2, 0, 0, 0, 1, est_cost=1.0, act_cost=3.5)
        self.assertEqual(bucket["cost_usd"], 3.5)

    def test_estimated_used_when_no_actual(self):
        bucket = sources._bucket()
        sources._bump(bucket, 10, 2, 0, 0, 0, 1, est_cost=0.25, act_cost=0.0)
        self.assertEqual(bucket["cost_usd"], 0.25)


class LimitParserTests(unittest.TestCase):
    def test_commandcode_windows_and_credits(self):
        parsed = sources.parse_commandcode_limits({
            "credits": {"monthlyCredits": 9.5, "purchasedCredits": 2, "freeCredits": 1},
            "windowLimits": {
                "fiveHour": {"used": 1.5, "cap": 3, "resetAt": 1000, "exceeded": False},
                "weekly": {"used": 3, "cap": 6, "resetAt": 2000, "exceeded": False},
            },
        })
        self.assertTrue(parsed["available"])
        self.assertEqual([w["used_pct"] for w in parsed["windows"]], [50.0, 50.0])
        self.assertEqual(parsed["monthly_credits_remaining"], 9.5)

    def test_openrouter_unlimited_key_still_shows_spend(self):
        parsed = sources.parse_openrouter_limits({"data": {
            "limit": None,
            "limit_remaining": None,
            "usage": 12.5,
            "usage_daily": 1.0,
            "usage_weekly": 4.0,
            "usage_monthly": 9.0,
            "is_free_tier": False,
        }})
        self.assertTrue(parsed["available"])
        self.assertEqual(parsed["windows"], [])
        self.assertEqual(parsed["usage_monthly"], 9.0)

    def test_openrouter_bounded_key_gets_limit_bar(self):
        parsed = sources.parse_openrouter_limits({"data": {
            "limit": 100,
            "limit_remaining": 25,
            "usage": 75,
            "usage_daily": 1,
            "usage_weekly": 5,
            "usage_monthly": 20,
        }})
        self.assertEqual(parsed["windows"][0]["used_pct"], 75.0)


if __name__ == "__main__":
    unittest.main()
