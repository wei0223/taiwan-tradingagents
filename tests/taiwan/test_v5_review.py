"""V5 contracts: known corrections, sensitivity, revision bridges and portability."""

import base64
import copy
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.taiwan.test_financial_periods import CUTOFF, ledger
from tradingagents.dataflows.taiwan.periods import resolve, superseded_documents
from tradingagents.research.bundle import fingerprint
from tradingagents.research.delivery import audit_package, seal
from tradingagents.research.event_status import inventory
from tradingagents.research.forecast_journal import error_summary, evaluate, register
from tradingagents.research.model_revision import bridge, compare
from tradingagents.research.quality import audit_html
from tradingagents.research.v5_review import historical_pe, parameter_dimensions


def inputs():
    return {'revenue_twd': 100, 'variable_cogs_ratio': .6, 'fixed_cogs_ex_depreciation_twd': 0,
                'cogs_depreciation_twd': 0, 'variable_opex_ratio': .1,
                'fixed_opex_ex_depreciation_twd': 0, 'opex_depreciation_twd': 0,
                'other_operating_twd': 0, 'nonoperating_twd': -2, 'tax_rate': .2,
                'noncontrolling_profit_fraction': .1, 'diluted_adjustment_twd': 0,
                'diluted_weighted_shares': 10}


class RevisionTests(unittest.TestCase):
    def test_telescopes_all_drivers(self):
        old = inputs()
        new = {**old, 'revenue_twd': 130, 'tax_rate': .3, 'nonoperating_twd': 2,
               'noncontrolling_profit_fraction': .2, 'diluted_weighted_shares': 12}
        result = bridge(old, new)
        self.assertTrue(result['reconciled'])
        self.assertAlmostEqual(sum(s['delta']['eps_twd'] for s in result['steps']), result['total_delta']['eps_twd'])

    def test_3231_parent_change_is_nonoperating_normalization(self):
        old = {**inputs(), 'revenue_twd': 1241016735693.3677,
               'variable_cogs_ratio': .9434205742559834, 'variable_opex_ratio': .01922663126857478,
               'nonoperating_twd': -6692742000, 'tax_rate': .23613572830404284,
               'noncontrolling_profit_fraction': .3755321617625196,
               'diluted_weighted_shares': None, 'diluted_adjustment_twd': None}
        new = {**old, 'nonoperating_twd': -2362979000, 'diluted_weighted_shares': 3649657000,
               'diluted_adjustment_twd': -11800000}
        result = bridge(old, new)
        self.assertAlmostEqual(result['before']['parent_profit_twd'], 18919452303.461906, places=4)
        self.assertAlmostEqual(result['after']['parent_profit_twd'], 20984786795.467728, places=4)
        self.assertAlmostEqual(result['total_delta']['parent_profit_twd'], 2065334492.0058212, places=4)
        self.assertIsNone(result['total_delta']['eps_twd'])
        self.assertEqual(result['total_delta']['operating_profit_twd'], 0)

    def test_structural_changes_not_silently_attributed(self):
        new = inputs()
        new['noncontrolling_profit_twd'] = new.pop('noncontrolling_profit_fraction')
        with self.assertRaises(ValueError):
            bridge(inputs(), new)

    def test_unknown_revenue_cannot_be_filled_zero(self):
        with self.assertRaises(ValueError):
            bridge({**inputs(), 'revenue_twd': None}, inputs())

    def test_incomplete_prior_version_does_not_prevent_other_current_research(self):
        def model(values):
            return {'scenarios': {'base': {'quarters': [{'period': '2026-Q4',
                    'inputs': {k: {'value':v} for k,v in values.items()}}]}}}
        result = compare(model({**inputs(), 'nonoperating_twd': None}), model(inputs()))
        self.assertFalse(result['rows'][0]['reconciled'])
        self.assertEqual(result['rows'][0]['steps'], [])


class CorrectionsTests(unittest.TestCase):
    def corrected(self, captured='2026-10-25T14:30:00+00:00'):
        review = ledger()
        doc = copy.deepcopy(review['documents']['doc'])
        raw = b'new corrected official mock original'
        doc.update(content_base64=base64.b64encode(raw).decode(), sha256=hashlib.sha256(raw).hexdigest(),
                   supersedes='doc', published_at='2026-10-25T14:20:00+00:00', captured_at=captured)
        review['documents']['corrected'] = doc
        return review

    def test_known_correction_invalidates_old_numbers(self):
        result = resolve(self.corrected(), '3017', CUTOFF)
        self.assertEqual(result['rows'], [])
        self.assertEqual(result['superseded_documents'], {'doc': 'corrected'})

    def test_future_capture_cannot_invalidate_old_historical_selection(self):
        review = self.corrected('2026-10-26T14:30:00+00:00')
        self.assertEqual(superseded_documents(review['documents'], '3017', CUTOFF), {})

    def test_wrong_company_cannot_replace_original(self):
        review = self.corrected()
        review['documents']['corrected']['stock_id'] = '3231'
        self.assertTrue(resolve(review, '3017', CUTOFF)['rows'])

    def test_bad_hash_cannot_certify_replacement(self):
        review = self.corrected()
        review['documents']['corrected']['sha256'] = '0'*64
        self.assertEqual(superseded_documents(review['documents'], '3017', CUTOFF), {})

    def test_source_packet_rejects_old_current_value(self):
        from tradingagents.research.review import Unavailable, _packet
        from tradingagents.research.scenarios import timestamp
        review = self.corrected()
        packet = {'stock_id': '3017', 'reviewed_by': 'fixture', 'values_reviewed': True,
                  'locator': 'mock page', 'version_set': 'mock', 'scope': 'consolidated',
                  'unit': 'TWD_and_shares', 'document_ids': ['doc'], 'values': {'revenue_twd': 1}}
        with self.assertRaises(Unavailable):
            _packet(packet, '3017', review['documents'], timestamp(CUTOFF), ('revenue_twd',))

    def test_reviewed_replacement_recomputes_not_old_cache(self):
        review = self.corrected()
        for r in review['observations']:
            r['document_ids'] = ['corrected']
        for b in review['bridges']:
            b['document_sha256s'] = [review['documents']['corrected']['sha256']]
        h1 = next(r for r in review['observations'] if r['id'] == 'h1.Revenue')
        h1['value'] += 100
        result = resolve(review, '3017', CUTOFF)
        q2 = next(r for r in result['rows'] if r['field'] == 'Revenue' and r['period_end'] == '2026-06-30')
        self.assertEqual(q2['value'], 49120964100)


class EventTests(unittest.TestCase):
    def plan(self, row):
        return {'context': {'cutoff_at': CUTOFF}, 'completeness': {'official_index_reconciliation':
                {'unprocessed_events': [row], 'categories': {}, 'revision_links': []}}}

    def test_published_missing_not_not_yet(self):
        row = {'id': 'official', 'published_at': '2026-10-20T10:00:00+08:00', 'categories': ['earnings']}
        out = inventory(self.plan(row), {}, {})
        self.assertEqual(out['events'][0]['status'], 'MISSING_PUBLISHED')
        self.assertTrue(out['events'][0]['hard_block'])

    def test_future_official_event_is_not_yet(self):
        row = {'id': 'official', 'published_at': '2026-11-20T10:00:00+08:00', 'categories': ['earnings']}
        out = inventory(self.plan(row), {}, {})
        self.assertEqual(out['events'][0]['status'], 'NOT_YET_PUBLISHED')
        self.assertFalse(out['events'][0]['hard_block'])

    def test_analyst_expected_date_not_official_nonpublication(self):
        plan = self.plan({'id': 'known', 'published_at': '2026-10-20', 'categories': []})
        spec = {'inventory': [{'id': 'expected', 'published_at': '2026-11-20', 'kind': 'earnings'}]}
        rows = inventory(plan, {}, spec)['events']
        self.assertEqual(next(r for r in rows if r['source_id'] == 'expected')['status'], 'SOURCE_UNAVAILABLE')

    def test_absence_or_unknown_time_not_nonpublication(self):
        row = {'id': 'official', 'categories': ['earnings']}
        self.assertEqual(inventory(self.plan(row), {}, {})['events'][0]['status'], 'SOURCE_UNAVAILABLE')

    def test_correction_status_and_untrusted_nonmaterial_flag(self):
        row = {'id': 'fix', 'published_at': '2026-10-20', 'categories': ['corrections']}
        out = inventory(self.plan(row), {}, {'event_impact_reviews': {'fix': {'material': False}}})
        self.assertEqual(out['events'][0]['status'], 'REVISION_UNRESOLVED')
        self.assertTrue(out['events'][0]['hard_block'])

    def test_source_exhaustion_gap_retained(self):
        plan = self.plan({'id': 'x', 'categories': []})
        plan['completeness']['official_index_reconciliation']['categories'] = {'capital': {'state': 'index_exhaustion_unverified'}}
        self.assertIn('scope:capital', inventory(plan, {}, {})['hard_blockers'])


class EvidenceTests(unittest.TestCase):
    def test_completeness_never_promoted_to_calibration(self):
        values = inputs()
        model = {'scenarios': {'base': {'quarters': [{'period': '2026-Q4', 'inputs': {k: {'value': v} for k, v in values.items()}}]}}}
        register = [{'scenario': 'base', 'period': '2026-Q4', 'parameters': [
            {'parameter_name': k, 'value': v, 'source': [], 'validity_status': 'reviewed', 'rationale': 'explicit anchor'} for k,v in values.items()]}]
        rows = parameter_dimensions(model, register, {}, CUTOFF)
        self.assertEqual(len(rows), len(values))
        self.assertTrue(all(r['dimensions']['completeness'] == 'PASS' and r['dimensions']['forecast_calibration'] == 'FAIL' for r in rows))
        self.assertTrue(all(r['alternatives'] and r['sensitivity'] for r in rows))

    def test_no_claimed_historical_pe_from_vendor_or_flags(self):
        row = {'stock_id': '2330', 'date': '2026-10-22', 'close': 100, 'captured_at': CUTOFF,
               'price_basis': 'unadjusted_TWD_per_common_share', 'source_sha256': '0'*64}
        out = historical_pe([row], [], '2330', CUTOFF)
        self.assertEqual(out['verified_points'], 0)
        self.assertIsNone(out['rows'][0]['pe'])
        self.assertIsNone(out['undervaluation_rank'])

    def test_source_bound_matching_historical_pe_and_future_capture_rejection(self):
        doc = copy.deepcopy(ledger('2330')['documents']['doc'])
        doc.update(captured_at='2026-10-20T10:00:00+08:00')
        proof = {'document': doc, 'source_sha256': doc['sha256'], 'source_url': doc['source_url'],
                 'reviewed_by': 'explicit mock original review', 'locator': 'mock page'}
        eps = [{**proof, 'stock_id': '2330', 'period': period,
                'basis': 'direct_reported_single_quarter_diluted_eps',
                'original_bytes_verified': True, 'published_at': doc['published_at'],
                'captured_at': doc['captured_at'], 'eps_twd': 3} for period in ['2025-Q3','2025-Q4','2026-Q1','2026-Q2']]
        raw = {'close': 120, 'date': '2026-10-22'}
        price = {'stock_id': '2330', 'date': raw['date'], 'close': raw['close'], 'raw': raw,
                 'source_sha256': fingerprint(raw), 'price_basis': 'unadjusted_TWD_per_common_share',
                 'captured_at': '2026-10-22T16:00:00+08:00', 'corporate_action_review': proof,
                 'expected_latest_reported_period': '2026-Q2'}
        self.assertEqual(historical_pe([price], eps, '2330', CUTOFF)['rows'][0]['pe'], 10)
        eps[0]['document'] = {**doc,'captured_at': '2026-10-25T14:00:00+00:00'}
        self.assertEqual(historical_pe([price], eps, '2330', CUTOFF)['verified_points'], 0)

    def test_pending_errors_do_not_become_performance(self):
        out = error_summary([{'status': 'pending_future_actuals'}])
        self.assertEqual(out['n'], 0)
        self.assertIsNone(out['mae'])

    def test_official_parameter_actuals_flow_into_metrics(self):
        doc = ledger()['documents']['doc']
        release = {'stock_id':'3017','period':'2026-Q2','metric':'eps','unit':'TWD/share',
                   'basis':'diluted_single_quarter','value':24,'issued_at':'2026-04-01T00:00:00+08:00'}
        release['release_sha256'] = fingerprint(release)
        actual = {**{k:release[k] for k in ('stock_id','period','metric','unit','basis')},
                  'value':23.92,'source_sha256':doc['sha256'],'locator':'mock EPS column',
                  'revision_id':doc['revision_id'],'published_at':doc['published_at'],'captured_at':doc['captured_at'],
                  'initial_publication_verified':True,'vintage_kind':'initial','source_value_reviewed':True,
                  'official_source_reviewed':True,'initial_publication_history_verified':True,'revision_policy':'first_official_release'}
        packet = {'stock_id':'3017','basis':'single_quarter_diluted','period_start':'2026-04-01','period_end':'2026-06-30',
                  'scope':'consolidated','unit':'TWD_and_shares','document_ids':['doc'],'reviewed_by':'fixture',
                  'values_reviewed':True,'locator':'mock EPS column','version_set':'mock','values':{'value':23.92}}
        result=evaluate([{'release':release,'actual':actual,'source_packet':packet}],'3017',{'doc':doc},CUTOFF)
        self.assertEqual(result['n'],1)
        self.assertAlmostEqual(result['mae'],.08)
        actual['initial_publication_verified']=False
        self.assertEqual(evaluate([{'release':release,'actual':actual,'source_packet':packet}],'3017',{'doc':doc},CUTOFF)['n'],0)

    def test_duplicate_scores_cannot_inflate_samples(self):
        row={'status':'scored_selected_initial_actual','release_sha256':'x','source_sha256':'y',
             'revision_id':'v1','evaluated_at':CUTOFF,'error':1}
        with self.assertRaises(ValueError):
            error_summary([row,row])

    def test_eps_performance_cannot_mix_revenue_units(self):
        pair={'release':{'stock_id':'3017','metric':'revenue','unit':'TWD','basis':'single_quarter'},
              'actual':{'stock_id':'3017'}}
        with self.assertRaises(ValueError):
            evaluate([pair],'3017',{},CUTOFF)

    def test_revenue_diagnostic_never_uses_other_company_history(self):
        from tradingagents.research.v5_review import revenue_diagnostic
        with self.assertRaises(ValueError):
            revenue_diagnostic({'instrument':{'stock_id':'3231'},'as_of':'2026-10-20'},'3017',CUTOFF)

    def test_unbound_scoring_provenance_rejected(self):
        with self.assertRaises(ValueError):
            error_summary([{'status': 'scored_selected_initial_actual', 'error': 1}])

    def test_zero_error_metrics_not_win_probability(self):
        r = {'status': 'scored_selected_initial_actual', 'release_sha256': 'x', 'source_sha256': 'y', 'revision_id': 'v1', 'evaluated_at': CUTOFF}
        out = error_summary([{**r,'error': 0},{**r,'release_sha256':'second','error': -2}])
        self.assertEqual(out['mae'], 1)
        self.assertFalse(out['empirical_advantage_proven'])


class PortableTests(unittest.TestCase):
    def test_bare_machine_paths_rejected_but_https_allowed(self):
        self.assertEqual(audit_html('<p>C:/Users/private/report.json</p>')['status'], 'failed')
        self.assertEqual(audit_html('<a href="https://example.org">source</a>')['status'], 'passed')

    def test_relocated_package_links_and_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'original'
            root.mkdir()
            (root/'research.json').write_text('{}')
            (root/'data.json').write_text('{}')
            (root/'report.html').write_text('<h1 id="a">Report</h1><a href="#a">top</a><a href="data.json">data</a><a href="delivery-manifest.json">manifest</a>')
            seal(root)
            moved = Path(tmp)/'different-location'
            shutil.copytree(root,moved)
            self.assertEqual(audit_package(moved)['status'], 'passed')
            manifest = json.loads((moved/'delivery-manifest.json').read_text())
            self.assertEqual(manifest['files']['data.json'], hashlib.sha256(b'{}').hexdigest())

    def test_missing_local_target_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'report.html').write_text('<a href="absent.json">missing</a>')
            self.assertEqual(audit_package(root)['status'], 'failed')


class JournalTests(unittest.TestCase):
    def files(self, root):
        model = {'stock_id': '2330', 'scenarios': {}}
        model['model_sha256'] = fingerprint(model)
        model_path = root/'model.json'
        model_path.write_text(json.dumps(model))
        receipt = {'schema_version': 'conditional-model-release-v1', 'stock_id': '2330',
                   'published_at': '2026-10-25T13:59:16+00:00', 'knowledge_cutoff_at': '2026-10-25T13:47:14+00:00',
                   'model_sha256': model['model_sha256'], 'model_file_sha256': hashlib.sha256(model_path.read_bytes()).hexdigest(),
                   'predictions': []}
        receipt['release_sha256'] = fingerprint(receipt)
        path = root/'release.json'
        path.write_text(json.dumps(receipt))
        return path, model_path

    def test_registration_idempotent_preserves_actual_original_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            release, model = self.files(root)
            result = register(root/'journal', release, model)
            before = result.read_bytes()
            self.assertEqual(register(root/'journal', release, model).read_bytes(), before)
            self.assertEqual(json.loads(before)['issued_at'], '2026-10-25T13:59:16+00:00')
            self.assertFalse(json.loads(before)['new_forecast_issued'])

    def test_modified_conditional_model_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            release, model = self.files(root)
            model.write_text('{}')
            with self.assertRaises((ValueError, KeyError)):
                register(root/'journal', release, model)

    def test_modified_journal_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            release, model = self.files(root)
            path = register(root/'journal', release, model)
            data = json.loads(path.read_text())
            data['issued_at'] = '2026-01-01T00:00:00Z'
            path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                register(root/'journal', release, model)

    def test_cross_html_missing_anchor_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'report.html').write_text('<a href="other.html#absent">missing</a>')
            (root/'other.html').write_text('<h1 id="present">Report</h1>')
            self.assertEqual(audit_package(root)['status'], 'failed')

    def test_encoded_escape_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'report.html').write_text('<a href="%2e%2e/secret.json">escape</a>')
            self.assertEqual(audit_package(root)['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
