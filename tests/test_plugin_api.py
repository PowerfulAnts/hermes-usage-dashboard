"""API response-shape tests for the desktop plugin contract."""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "dashboard"))

import plugin_api  # noqa: E402


class SummaryContractTests(unittest.TestCase):
    def test_summary_keeps_tokens_limits_and_portal_separate(self):
        tokens = {
            "available": True,
            "meta": {"scope": "hermes-only"},
            "totals": {"input": 10, "output": 2, "cached": 8,
                       "cache_write": 0, "hit_rate_pct": 44.4},
            "providers": {},
            "daily": {},
        }
        limits = {"providers": {"openrouter": {"available": True}}}
        portal = {"available": True, "total_spendable_display": "$1.00"}

        with (
            patch("sources.collect", return_value=tokens),
            patch("sources.collect_limits", return_value=limits),
            patch.object(plugin_api, "_portal_model", return_value=portal),
        ):
            result = asyncio.run(plugin_api.summary(30))

        self.assertEqual(result["meta"]["scope"], "hermes-only")
        self.assertIn("openrouter", result["limits"]["providers"])
        self.assertIn("nous", result["limits"]["providers"])
        self.assertEqual(result["portal"]["total_spendable_display"], "$1.00")


if __name__ == "__main__":
    unittest.main()