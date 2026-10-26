import base64
import copy
import hashlib
import json
import unittest

from tradingagents.research.gap_closure import audit, render


class GapClosureTests(unittest.TestCase):
    def setUp(self):
        body = b'official current list; no event/version mapping'
        digest = hashlib.sha256(body).hexdigest()
        self.docs = {'list': {'stock_id': '3231', 'url': 'https://doc.twse.com.tw/list',
                             'sha256': digest, 'captured_at': '2026-10-25T10:00:00+00:00',
                             'content_base64': base64.b64encode(body).decode()}}
        self.item = {'id': '3231-E01', 'stock_id': '3231', 'event_id': 'exact_event',
                     'disposition': 'unreconstructable_in_reviewed_scope',
                     'target_document': '2026-09 prospectus event-to-version record',
                     'missing_fields': ['event_id->version->bytes'],
                     'search_scope': ['official prospectus index and current filing list'],
                     'finding': 'Current filename is known; historical event linkage is absent',
                     'finding_code': 'captured_official_lists_lack_event_version_binding',
                     'candidate_filenames': ['202609_3231_B11.pdf'],
                     'excluded_uses': ['strict_historical_performance'],
                     'reopen_requirements': ['official event/version record with byte identity'],
                     'evidence': [{'source_id': 'list', 'sha256': digest, 'locator': 'filing row'}]}
        self.ledger = {'schema_version': 1, 'reviewed_by': 'reviewer',
                       'reviewed_at': '2026-10-26T10:00:00+00:00', 'items': [self.item]}
        self.now = '2026-10-26T11:00:00+00:00'

    def run_audit(self):
        return audit(self.ledger, self.docs, self.now, '3231')

    def test_closed_work_is_not_pit_qualification(self):
        result = self.run_audit()
        self.assertEqual(result['work_states'], {'closed_scoped': 1})
        self.assertFalse(result['historical_performance_granted'])
        self.assertFalse(result['items'][0]['qualification_promoted'])
        self.assertFalse(result['items'][0]['original_missing_metric_available'])
        self.assertIn('缺口結案審查', render(result, lambda *_: 'table'))
        def table(headers, rows):
            self.assertIn('用途限制', headers)
            self.assertIn('重新開案條件', headers)
            self.assertIn('strict_historical_performance', rows[0][-2])
            self.assertIn('official event/version record', rows[0][-1])
            return 'table'
        render(result, table)

    def test_refusal_alone_cannot_close(self):
        self.item['finding_code'] = '403'
        with self.assertRaises(ValueError):
            self.run_audit()

    def test_explicit_fields_and_candidate_required(self):
        for field in ['target_document', 'missing_fields', 'search_scope', 'reopen_requirements', 'candidate_filenames']:
            with self.subTest(field=field):
                value = self.item.pop(field)
                with self.assertRaises(ValueError):
                    self.run_audit()
                self.item[field] = value

    def test_actual_bytes_must_match(self):
        self.docs['list']['content_base64'] = base64.b64encode(b'changed').decode()
        with self.assertRaises(ValueError):
            self.run_audit()

    def test_future_review_and_future_capture_rejected(self):
        self.now = '2026-10-26T09:00:00+00:00'
        with self.assertRaises(ValueError):
            self.run_audit()
        self.now = '2026-10-26T11:00:00+00:00'
        self.docs['list']['captured_at'] = '2026-10-27T10:00:00+00:00'
        with self.assertRaises(ValueError):
            self.run_audit()

    def test_company_and_official_host_required(self):
        self.docs['list']['stock_id'] = '3017'
        with self.assertRaises(ValueError):
            self.run_audit()
        self.docs['list']['stock_id'] = '3231'
        self.docs['list']['url'] = 'https://example.com/list'
        with self.assertRaises(ValueError):
            self.run_audit()

    def test_alternative_does_not_fill_quarter(self):
        self.item['disposition'] = 'alternative_scope'
        self.item['excluded_uses'] = ['Q4_diluted_EPS', 'rolling_TTM', 'strict_historical_performance']
        self.item['alternative'] = {'basis': 'direct_reported_annual_diluted_eps', 'period': '2025-FY',
                                    'reviewed_value': 8.40, 'source_id': 'list',
                                    'permitted_uses': ['same_fiscal_year_annual_comparison']}
        result = self.run_audit()
        self.assertFalse(result['items'][0]['original_missing_metric_available'])
        self.item['alternative']['permitted_uses'].append('rolling_TTM')
        with self.assertRaises(ValueError):
            self.run_audit()

    def test_recoverable_has_specific_next_action(self):
        self.item['disposition'] = 'recoverable'
        with self.assertRaises(ValueError):
            self.run_audit()
        self.item['next_action'] = 'Review already captured original p57 diluted numerator'
        self.assertEqual(self.run_audit()['work_states'], {'actionable': 1})

    def test_input_immutable_and_duplicate_rejected(self):
        before = copy.deepcopy(self.ledger)
        self.run_audit()
        self.assertEqual(before, self.ledger)
        self.ledger['items'].append(copy.deepcopy(self.item))
        with self.assertRaises(ValueError):
            self.run_audit()

    def test_repeated_capture_is_not_another_event(self):
        from tradingagents.dataflows.taiwan.publications import MopsAnnouncementClient
        from tradingagents.research.resolution import index_rows

        event = {'COMPANY_ID': '3231', 'HYPERLINK': 'https://mops.twse.com.tw/exact', 'SUBJECT': 'a'}
        body = json.dumps({'data': [event]}).encode()
        doc = {'source_url': MopsAnnouncementClient.endpoint,
               'sha256': hashlib.sha256(body).hexdigest(), 'content_base64': base64.b64encode(body).decode()}
        self.assertEqual(index_rows({'first': doc, 'later': copy.deepcopy(doc)}), [event])
        changed = {**event, 'SUBJECT': 'different announcement'}
        body = json.dumps({'data': [changed]}).encode()
        other = {**doc, 'sha256': hashlib.sha256(body).hexdigest(), 'content_base64': base64.b64encode(body).decode()}
        self.assertEqual(len(index_rows({'first': doc, 'changed': other})), 2)

    def test_presentation_groups_keep_distinct_reopen_requirements(self):
        from tradingagents.research.gap_closure import presentation_groups

        first = self.run_audit()['items'][0]
        same = {**copy.deepcopy(first), 'id': 'another-event'}
        different = {**copy.deepcopy(first), 'id': 'different-condition',
                     'reopen_requirements': ['quarter-specific EPS denominator note']}
        groups = presentation_groups({'items': [first, same, different]})
        self.assertEqual([len(g) for g in groups], [2, 1])
        self.assertEqual([i['id'] for g in groups for i in g], [first['id'], same['id'], different['id']])


if __name__ == '__main__':
    unittest.main()
