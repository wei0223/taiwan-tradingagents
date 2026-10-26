import base64
import copy
import hashlib
import tempfile
import unittest
from datetime import UTC, datetime, timedelta

from tradingagents.research.bundle import fingerprint
from tradingagents.research.prospective import publish
from tradingagents.research.publication import read_release
from tradingagents.research.tracking import read_tracking, track


class ProspectiveReleaseTests(unittest.TestCase):
    def setUp(self):
        raw = b'Synthetic issuer evidence, not real financial data'
        self.docs = {'note': {'stock_id': '2330', 'source_url': 'https://mops.twse.com.tw/note',
            'sha256': hashlib.sha256(raw).hexdigest(), 'content_base64': base64.b64encode(raw).decode(),
            'captured_at': '2026-10-25T10:00:00+00:00', 'published_at': None, 'public_capture_verified': True}}
        self.bundle = {'stock_id': '2330', 'as_of': '2026-10-26',
                       'knowledge_cutoff_at': '2026-10-26T01:00:00+00:00', 'facts_packet_sha256': 'synthetic'}
        values = {'revenue_twd': 1e9, 'variable_cogs_ratio': .4, 'fixed_cogs_ex_depreciation_twd': 0,
            'cogs_depreciation_twd': 0, 'variable_opex_ratio': .1, 'fixed_opex_ex_depreciation_twd': 0,
            'opex_depreciation_twd': 0, 'other_operating_twd': 0, 'nonoperating_twd': 0,
            'tax_rate': .2, 'noncontrolling_profit_fraction': .01, 'diluted_adjustment_twd': 0,
            'diluted_weighted_shares': 1e8}
        reviews = {k: {'kind': 'assumption', 'source_ids': ['note'], 'rationale': 'Synthetic assumption',
                       'uncertainty': 'Uncalibrated'} for k in values}
        self.model = {'stock_id': '2330', 'model_version': 'synthetic-v1', 'created_at': '2026-10-26T02:00:00+00:00',
            'quarters': [{'period': q, 'inputs': copy.deepcopy(values), 'parameter_review': copy.deepcopy(reviews)}
                         for q in ['2026-Q4', '2027-Q1', '2027-Q2', '2027-Q3']]}
        self.debate = {'complete': True, 'facts_sha256': 'synthetic', 'stages': {}}
        for stage, agent, hour in [('bull_opening', 'bull', 2), ('bear_opening', 'bear', 2),
                                   ('bull_reply', 'bull', 3), ('bear_reply', 'bear', 3), ('risk', 'risk', 4)]:
            content = {'synthetic': True, 'stage': stage, 'cross_examination': ['test'],
                       'stock_id': '2330', 'facts_sha256': 'synthetic'}
            self.debate['stages'][stage] = {'agent_id': agent, 'completed_at': f'2026-10-26T0{hour}:00:00+00:00',
                                          'content': content, 'artifact_sha256': fingerprint(content)}
        self.selections = [{'period': q['period'], 'metric': metric,
             'decision': 'publish_forecast' if q['period'] == '2026-Q4' else 'conditional_only',
             'reviewed_by': 'synthetic-risk', 'reason': 'Explicit selected scope',
             'uncalibrated_acknowledged': True, 'denominator_assumption_review': 'issuer anchored assumption',
             'anti_dilution_policy': 'Review actual note, do not difference EPS', 'numerator_policy': 'no double adjustment'}
             for q in self.model['quarters'] for metric in ['eps', 'revenue']]
        self.now = datetime(2026, 10, 26, 5, tzinfo=UTC)
        risk = self.debate['stages']['risk']
        risk['content'].update(stock_id='2330', selected_model=copy.deepcopy(self.model),
            publication_decisions=[{'period': q['period'], 'eps': 'publish_forecast' if q['period']=='2026-Q4' else 'conditional_only',
                                   'revenue': 'publish_forecast' if q['period']=='2026-Q4' else 'conditional_only'}
                                  for q in self.model['quarters']])
        risk['artifact_sha256'] = fingerprint(risk['content'])

    def issue(self, folder):
        return publish(folder, self.bundle, self.model, self.debate, self.selections, self.docs, lambda: self.now)

    def test_selected_metrics_only_immutable_and_tracking_compatible(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.issue(folder)
            released = read_release(path)
            self.assertEqual(len(released['predictions']), 2)
            self.assertFalse(released['formal_valuation_qualified'])
            self.assertFalse(released['historical_performance_qualified'])
            self.assertEqual(released['predictions'][0]['value'], 3.96)
            result = read_tracking(track(path, [], folder+'/tracking', lambda: self.now))
            self.assertEqual(len(result['results']['pending']), 2)
            with self.assertRaises(FileExistsError):
                self.issue(folder)
            self.now += timedelta(minutes=1)
            with self.assertRaises(FileExistsError):
                self.issue(folder)

    def test_unreviewed_future_input_cannot_be_fact(self):
        self.model['quarters'][0]['parameter_review']['tax_rate']['kind'] = 'fact'
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(ValueError):
            self.issue(folder)

    def test_invalid_or_future_source_rejected(self):
        for field, value in [('sha256', '0'*64), ('captured_at', '2026-10-27T00:00:00+00:00'),
                             ('stock_id', '3017'), ('public_capture_verified', False)]:
            with self.subTest(field=field):
                before = self.docs['note'][field]
                self.docs['note'][field] = value
                with tempfile.TemporaryDirectory() as folder, self.assertRaises(ValueError):
                    self.issue(folder)
                self.docs['note'][field] = before

    def test_source_bytes_and_forecast_cannot_be_replaced(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.issue(folder)
            released = read_release(path)
            source = path.parent / released['sources']['note']['file']
            source.write_bytes(b'changed')
            with self.assertRaises(ValueError):
                read_release(path)

    def test_no_empty_forecast_release(self):
        for row in self.selections:
            row['decision'] = 'conditional_only'
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(ValueError):
            self.issue(folder)

    def test_eps_needs_share_and_numerator_policy(self):
        self.selections[0].pop('anti_dilution_policy')
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(ValueError):
            self.issue(folder)

    def test_every_quarter_metric_needs_explicit_disposition(self):
        self.selections.pop()
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(ValueError):
            self.issue(folder)

    def test_distinct_independent_roles_required(self):
        self.debate['stages']['risk']['agent_id'] = 'bull'
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(ValueError):
            self.issue(folder)

    def test_debate_artifact_and_cross_examination_binding(self):
        for field, value in [('artifact_sha256', '0'*64), ('content', {})]:
            with self.subTest(field=field):
                before = self.debate['stages']['bull_reply'][field]
                self.debate['stages']['bull_reply'][field] = value
                with tempfile.TemporaryDirectory() as folder, self.assertRaises(ValueError):
                    self.issue(folder)
                self.debate['stages']['bull_reply'][field] = before

    def test_reply_cannot_precede_both_openings(self):
        self.debate['stages']['bull_reply']['completed_at'] = '2026-10-26T01:00:00+00:00'
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(ValueError):
            self.issue(folder)

    def test_ended_quarter_cannot_be_republished_prospectively(self):
        self.now = datetime(2027, 1, 1, 0, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(ValueError):
            self.issue(folder)

    def test_conditional_ruling_cannot_be_upgraded_by_publisher(self):
        self.selections[-1]['decision'] = 'publish_forecast'
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(ValueError):
            self.issue(folder)

    def test_adjudicated_numeric_inputs_cannot_be_changed(self):
        self.model['quarters'][0]['inputs']['revenue_twd'] *= 1.01
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(ValueError):
            self.issue(folder)

    def test_opening_company_and_facts_packet_must_match(self):
        for key, value in [('stock_id', '3017'), ('facts_sha256', 'different-packet')]:
            with self.subTest(key=key):
                stage = self.debate['stages']['bull_opening']
                original = stage['content'][key]
                stage['content'][key] = value
                stage['artifact_sha256'] = fingerprint(stage['content'])
                with tempfile.TemporaryDirectory() as folder, self.assertRaises(ValueError):
                    self.issue(folder)
                stage['content'][key] = original
                stage['artifact_sha256'] = fingerprint(stage['content'])


if __name__ == '__main__':
    unittest.main()
