import unittest
from datetime import UTC, datetime
from unittest.mock import patch

from tradingagents.research.review import review


class FixedTechnicalGateTests(unittest.TestCase):
    def test_failed_fixed_gate_never_calls_legacy_short_calculator(self):
        with patch('tradingagents.research.review.short_review',side_effect=AssertionError('must not calculate')):
            result=review('1101','2026-11-04',snapshot={'unusable':'raw prices'},technical_gate={'status':'blocked_history'},clock=lambda:datetime(2026,11,4,9,tzinfo=UTC))
        trade=result['axes']['short_term']
        self.assertEqual(trade['status'],'unavailable')
        self.assertEqual(trade['reason'],'fixed_technical_gate_failed')
        self.assertNotIn('provisional_rr',trade)
        self.assertNotIn('stop_twd',trade)

    def test_ready_and_nonfixed_reports_keep_original_short_review(self):
        for gate in [None,{'status':'ready'}]:
            with self.subTest(gate=gate),patch('tradingagents.research.review.short_review',return_value={'status':'observe'}) as calculator:
                result=review('2330','2026-11-04',snapshot={'existing':'workflow'},technical_gate=gate,clock=lambda:datetime(2026,11,4,9,tzinfo=UTC))
            calculator.assert_called_once()
            self.assertEqual(result['axes']['short_term']['status'],'observe')
