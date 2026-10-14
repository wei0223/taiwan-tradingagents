"""Synthetic official filing documents; no network or actual company PDFs."""

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from tradingagents.dataflows.taiwan.filings import MopsFilingArchive

NOW = datetime(2024, 5, 20, 12, tzinfo=UTC)


class Response:
    status_code = 200

    def __init__(self, content):
        self.content = content


class Source:
    def __init__(self):
        self.calls = []
        self.pdf = b'%PDF-1.7 SYNTHETIC DOCUMENT VERSION'
        self.upload = '113/05/10 14:30:00'
        self.link = '/pdf/202401_2330_AI1_20240520_120000.pdf'

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if '/pdf/' in url:
            return Response(self.pdf)
        return Response(('<table><tr><th>電子檔案</th><th>上傳日期</th><th>財務報告更(補)正</th></tr>'
                         '<tr><td><a href=\'javascript:readfile2("A","2330","202401_2330_AI1.pdf");\'>檔案</a>'
                         '</td><td>' + self.upload + '</td><td>無</td></tr></table>').encode('big5'))

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return Response(('<a href="' + self.link + '">PDF</a>').encode('big5'))


class FilingTests(unittest.TestCase):
    def setUp(self):
        self.socket = patch('socket.socket.connect', side_effect=AssertionError('No network'))
        self.socket.start()
        self.addCleanup(self.socket.stop)

    def test_bytes_corrections_retained_and_tamper_detected(self):
        source = Source()
        with tempfile.TemporaryDirectory() as root:
            archive = MopsFilingArchive(root, source, lambda: NOW)
            first = archive.capture('2330', 2024, '2024-05-20')
            receipt = archive.verify(first)
            self.assertEqual(len(receipt['documents']), 1)
            first_pdf = receipt['documents'][0]['pdf_sha256']
            source.pdf = b'%PDF-1.7 SYNTHETIC CORRECTION'
            second = archive.capture('2330', 2024, '2024-05-20')
            self.assertNotEqual(first, second)
            self.assertEqual(len(list((Path(root) / '2330').glob('*.pdf'))), 2)
            self.assertEqual(archive.verify(first)['documents'][0]['pdf_sha256'], first_pdf)
            (Path(root) / '2330' / (first_pdf + '.pdf')).write_bytes(b'corrupt')
            with self.assertRaises(ValueError):
                archive.verify(first)
        for _, kwargs in source.calls:
            self.assertNotIn('headers', kwargs)
            self.assertFalse(kwargs['allow_redirects'])

    def test_future_upload_and_future_capture_not_backdated(self):
        with tempfile.TemporaryDirectory() as root:
            source = Source()
            archive = MopsFilingArchive(root, source, lambda: NOW)
            self.assertFalse(archive.verify(archive.capture('2330', 2024, '2024-05-19'))['documents'])
            source.upload = '113/05/21 14:30:00'
            self.assertFalse(archive.verify(archive.capture('2330', 2024, '2024-05-20'))['documents'])

    def test_unsafe_download_and_non_pdf_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            source = Source()
            source.link = 'https://example.org/token'
            archive = MopsFilingArchive(root, source, lambda: NOW)
            with self.assertRaises(ValueError):
                archive.capture('2330', 2024, '2024-05-20')
            source.link = '/pdf/202401_2330_AI1_20240520_120000.pdf'
            source.pdf = b'<html>SYNTHETIC ERROR</html>'
            with self.assertRaises(ValueError):
                archive.capture('2330', 2024, '2024-05-20')


if __name__ == '__main__':
    unittest.main()
