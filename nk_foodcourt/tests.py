from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image

from menu.models import Category, MenuItem


class PublicMenuImageTests(TestCase):
    def setUp(self):
        self.media = TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.override = override_settings(MEDIA_ROOT=self.media.name)
        self.override.enable()
        self.addCleanup(self.override.disable)
        data = BytesIO()
        Image.new('RGB', (80, 80), '#de8822').save(data, format='PNG')
        self.item = MenuItem.objects.create(
            category=Category.objects.create(name='Test dishes', slug='test-dishes'),
            name='Uploaded dish', price='75.00',
            image=SimpleUploadedFile('dish.png', data.getvalue(), content_type='image/png'),
        )

    def test_referenced_dish_image_is_available_with_debug_off(self):
        with override_settings(DEBUG=False):
            response = self.client.get(self.item.image.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'image/png')
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        self.assertTrue(b''.join(response.streaming_content).startswith(b'\x89PNG'))

    def test_unreferenced_and_private_paths_are_not_served(self):
        Path(self.media.name, 'menu', 'unreferenced.png').write_bytes(b'not public')
        for url in ['/media/menu/unreferenced.png', '/media/private/payment_qr/secret.png', '/media/menu/../private/secret.png']:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)

    def test_non_image_content_is_rejected_even_if_referenced(self):
        Path(self.item.image.path).write_bytes(b'<html><script>alert(1)</script></html>')
        self.assertEqual(self.client.get(self.item.image.url).status_code, 404)

    def test_image_endpoint_is_read_only(self):
        self.assertEqual(self.client.post(self.item.image.url).status_code, 405)
