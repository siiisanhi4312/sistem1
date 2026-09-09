import io
from datetime import date
from unittest.mock import patch

import numpy as np
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from PIL import Image

from prediction.views import _ensure_probabilities
from prediction.models import ShellfishAdvisory


class PredictViewTests(TestCase):
    def test_softmax_normalizes_logits_before_confidence_is_reported(self):
        logits = np.array([3.0, 1.0, -1.0, -2.0], dtype=np.float32)
        probabilities = _ensure_probabilities(logits)

        self.assertEqual(probabilities.shape, logits.shape)
        self.assertAlmostEqual(float(probabilities.sum()), 1.0, places=6)
        self.assertTrue(np.all(probabilities >= 0.0))
        self.assertTrue(np.all(probabilities <= 1.0))
        self.assertGreater(float(probabilities[0]), 0.5)
    
    def test_ensure_probabilities_handles_already_softmax_output(self):
        """Test that already-normalized output (sum=1.0) is not double-normalized."""
        # Model output that already sums to 1.0 (from softmax layer)
        probabilities_input = np.array([0.1, 0.41, 0.44, 0.05], dtype=np.float32)
        probabilities = _ensure_probabilities(probabilities_input)
        
        # Should remain approximately the same (not double-softmaxed)
        self.assertAlmostEqual(float(probabilities[2]), 0.44, places=3)  # max should stay ~0.44
        self.assertAlmostEqual(float(probabilities.sum()), 1.0, places=6)
        self.assertTrue(np.all(probabilities >= 0.0))
        self.assertTrue(np.all(probabilities <= 1.0))

    def test_predict_endpoint_accepts_image_and_returns_prediction(self):
        image = Image.new('RGB', (224, 224), color=(25, 35, 45))
        buffer = io.BytesIO()
        image.save(buffer, format='PNG')
        buffer.seek(0)

        uploaded_image = SimpleUploadedFile(
            'shell_test.png',
            buffer.read(),
            content_type='image/png',
        )

        response = self.client.post(
            reverse('predict_shell'),
            {'image': uploaded_image},
            format='multipart',
        )

        print('DEBUG_STATUS', response.status_code)
        print('DEBUG_BODY', response.content.decode())
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn('prediction', payload)
        self.assertIn('confidence', payload)
        self.assertIn('label', payload)
        self.assertIn('probabilities', payload)
        self.assertIsInstance(payload['probabilities'], dict)
        self.assertEqual(len(payload['probabilities']), 8)
        self.assertGreaterEqual(float(payload['confidence']), 0.0)
        self.assertLessEqual(float(payload['confidence']), 1.0)
        self.assertNotEqual(payload['prediction'], 'conus-textile')


class ShellfishAdvisoryTests(TestCase):
    @patch('prediction.services.bfar.firebase_bulletins.upsert')
    @patch('prediction.services.bfar.fetch_pdf')
    @patch('prediction.services.bfar._archive_candidates')
    @patch('prediction.services.bfar.requests.get')
    def test_sync_detects_bulletin_and_caches_pdf_metadata(
        self, request_get, archive_candidates, fetch_pdf_mock, upsert_mock,
    ):
        listing = type('Response', (), {
            'text': '<html>Shellfish Bulletin</html>',
            'content': b'html',
            'status_code': 200,
            'headers': {},
            'raise_for_status': lambda self: None,
        })()
        request_get.return_value = listing

        archive_candidates.return_value = [{
            'title': 'Shellfish Bulletin No. 12',
            'source_url': 'https://www.bfar.da.gov.ph/bulletin.html',
            'pdf_url': 'https://www.bfar.da.gov.ph/files/bulletin12.pdf',
            'pdf_filename': 'bulletin12.pdf',
            'pdf_content_type': 'application/pdf',
            'pdf_size': 128,
            'pdf_sha256': 'abc123',
            'status': 'OPEN',
            'affected_areas': ['Area A'],
            'important_information': 'Keep cautious.',
        }]
        fetch_pdf_mock.return_value = (
            type('Response', (), {'headers': {'Content-Type': 'application/pdf'}})(),
            {
                'status': 'OPEN',
                'affected_areas': ['Area A'],
                'important_information': 'Keep cautious.',
                'bulletin_number': '12',
                'date': date(2026, 8, 24),
                'pdf_text_extracted': True,
            },
        )

        from prediction.services.bfar import sync_latest_advisory
        result = sync_latest_advisory(force=True)

        self.assertTrue(result['changed'])
        advisory = ShellfishAdvisory.objects.get()
        self.assertEqual(advisory.bulletin_number, '12')
        self.assertTrue(advisory.pdf_url.endswith('bulletin12.pdf'))
        self.assertEqual(advisory.pdf_size, 128)
        upsert_mock.assert_called_once()

    def test_sync_reports_running_when_another_sync_is_in_progress(self):
        from prediction.services import bfar as bfar_service

        self.assertTrue(bfar_service._BFAR_SYNC_LOCK.acquire(blocking=False))
        try:
            result = bfar_service.sync_latest_advisory(force=True)
            self.assertTrue(result['running'])
            self.assertIn('already running', result['error'].lower())
        finally:
            bfar_service._BFAR_SYNC_LOCK.release()

    @patch('prediction.services.bfar.firebase_bulletins.upsert')
    @patch('prediction.services.bfar.fetch_pdf')
    @patch('prediction.services.bfar._archive_candidates')
    @patch('prediction.services.bfar.requests.get')
    def test_sync_uses_new_direct_homepage_bulletin_without_archive_crawl(
        self, request_get, archive_candidates, fetch_pdf_mock, upsert_mock,
    ):
        listing = type('Response', (), {
            'text': (
                '<a href="/files/SB-20-22-August-2026.pdf">'
                'Shellfish Bulletin No. 20 - August 22, 2026</a>'
            ),
            'content': b'html',
            'status_code': 200,
            'headers': {},
            'raise_for_status': lambda self: None,
        })()
        request_get.return_value = listing
        def fetch_latest(advisory):
            advisory.update({
                'pdf_filename': 'SB-20-22-August-2026.pdf',
                'pdf_content_type': 'application/pdf',
                'pdf_size': 128,
                'pdf_sha256': 'def456',
            })
            return (
                type('Response', (), {'headers': {'Content-Type': 'application/pdf'}})(),
                {
                    'status': 'OPEN',
                    'affected_areas': ['Area B'],
                    'important_information': 'Keep cautious.',
                    'bulletin_number': 'SB 20',
                    'date': date(2026, 8, 22),
                    'pdf_text_extracted': True,
                },
            )

        fetch_pdf_mock.side_effect = fetch_latest

        from prediction.services.bfar import sync_latest_advisory
        result = sync_latest_advisory(force=True)

        self.assertTrue(result['changed'])
        self.assertEqual(ShellfishAdvisory.objects.get().bulletin_number, 'SB 20')
        archive_candidates.assert_not_called()
        upsert_mock.assert_called_once()

    def test_advisory_endpoint_returns_cached_record_when_bfar_fails(self):
        advisory = ShellfishAdvisory.objects.create(
            bulletin_number='Bulletin 1',
            bulletin_year=2024,
            title='Shellfish Advisory',
            source_url='https://www.bfar.da.gov.ph/',
            pdf_url='https://www.bfar.da.gov.ph/advisory.pdf',
        )
        with patch('prediction.services.bfar.requests.get', side_effect=OSError('offline')):
            response = self.client.get(reverse('shellfish_advisory'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['advisory']['bulletin_number'], advisory.bulletin_number)
        self.assertTrue(response.json()['sync']['cached'])

    @patch('prediction.services.bfar.requests.get', side_effect=OSError('offline'))
    def test_advisory_endpoint_reports_unavailable_without_cache(self, _request):
        response = self.client.get(reverse('shellfish_advisory'))

        self.assertEqual(response.status_code, 503)
        self.assertIsNone(response.json()['advisory'])
