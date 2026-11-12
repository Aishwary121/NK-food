import tempfile
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from unittest import skipUnless
from urllib.parse import parse_qs, urlparse
from PIL import Image
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from groups.models import DiningGroup, Membership
from orders.models import BillAllocation, Order
from .forms import QRSettingsForm
from .models import Payment, PaymentAudit, PaymentQRSettings
from .services import build_upi_uri, generate_qr_png, get_payment, payment_totals, review_payment, save_qr_settings, submit_payment
from .validators import validate_upi_id

try:
    import zxingcpp
except ImportError:
    zxingcpp = None

def uploaded_image(name='qr.png', format='PNG', size=(320, 320), trailing=b''):
    buffer = BytesIO()
    Image.new('RGB', size, 'white').save(buffer, format=format)
    return SimpleUploadedFile(name, buffer.getvalue() + trailing, content_type='image/png')

class PaymentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model()
        cls.rahul = users.objects.create_user('rahul', first_name='Rahul', password='Secret@1234')
        cls.aman = users.objects.create_user('aman', first_name='Aman', password='Secret@1234')
        cls.outsider = users.objects.create_user('outsider', password='Secret@1234')
        cls.admin = users.objects.create_user('admin', is_staff=True, password='Secret@1234')
        cls.group = DiningGroup.objects.create(name='Dinner', owner=cls.rahul, collector=cls.rahul, status='FINALIZED')
        cls.rmember = Membership.objects.create(group=cls.group, user=cls.rahul)
        cls.amember = Membership.objects.create(group=cls.group, user=cls.aman)
        cls.order = Order.objects.create(group=cls.group, status='FINALIZED', subtotal='283.00', total='283.00')
        cls.rahul_share = BillAllocation.objects.create(order=cls.order, member=cls.rmember, amount='175.50')
        cls.aman_share = BillAllocation.objects.create(order=cls.order, member=cls.amember, amount='107.50')
        cls.config = PaymentQRSettings.objects.create(order=cls.order, upi_id='collector@bank', display_name='Rahul & Friends', preferred_method='DYNAMIC')

    def setUp(self):
        self.temp_media = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_media.cleanup)
        self.settings_override = override_settings(PRIVATE_MEDIA_ROOT=self.temp_media.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)

    def form_data(self, **kwargs):
        return {'upi_id': 'collector@bank', 'display_name': 'Rahul & Friends', 'preferred_method': 'DYNAMIC', **kwargs}

    def save_static(self):
        form = QRSettingsForm(self.form_data(preferred_method='STATIC'), {'static_qr': uploaded_image()}, instance=self.config)
        self.assertTrue(form.is_valid(), form.errors)
        return save_qr_settings(self.order.pk, self.rahul, form.cleaned_data)

    def test_upi_uri_exact_receiver_amount_currency_and_note(self):
        uri = build_upi_uri(self.rahul_share)
        self.assertTrue(uri.startswith('upi://pay?'))
        params = parse_qs(urlparse(uri).query)
        self.assertEqual(params['pa'], ['collector@bank'])
        self.assertEqual(params['pn'], ['Rahul & Friends'])
        self.assertEqual(params['am'], ['175.50'])
        self.assertEqual(params['cu'], ['INR'])
        self.assertEqual(params['tn'], [f'NK_FOODCOURT_{self.order.number}'])
        self.assertIn('%26', uri)
        self.assertNotIn(' ', uri)

    def test_different_members_receive_different_amounts_and_pngs(self):
        rahul_uri = build_upi_uri(self.rahul_share)
        aman_uri = build_upi_uri(self.aman_share)
        self.assertEqual(parse_qs(urlparse(aman_uri).query)['am'], ['107.50'])
        self.assertNotEqual(rahul_uri, aman_uri)
        first, second = generate_qr_png(rahul_uri), generate_qr_png(aman_uri)
        self.assertNotEqual(first, second)
        with Image.open(BytesIO(first)) as png:
            self.assertEqual(png.format, 'PNG')
            self.assertGreaterEqual(png.width, 320)
            png.verify()

    @skipUnless(zxingcpp, 'Install requirements-dev.txt to scan generated QR PNGs.')
    def test_generated_png_decodes_to_exact_upi_request(self):
        for allocation, amount in [(self.rahul_share, '175.50'), (self.aman_share, '107.50')]:
            uri = build_upi_uri(allocation)
            image = Image.open(BytesIO(generate_qr_png(uri)))
            decoded = zxingcpp.read_barcode(image)
            self.assertIsNotNone(decoded)
            self.assertEqual(decoded.text, uri)
            self.assertEqual(parse_qs(urlparse(decoded.text).query)['am'], [amount])
            self.assertEqual(parse_qs(urlparse(decoded.text).query)['pa'], ['collector@bank'])

    def test_equal_amounts_still_have_distinct_member_references(self):
        BillAllocation.objects.filter(pk=self.aman_share.pk).update(amount='175.50')
        self.assertNotEqual(build_upi_uri(self.rahul_share), build_upi_uri(self.aman_share))

    def test_in_memory_amount_override_is_ignored(self):
        self.aman_share.amount = Decimal('0.01')
        self.assertEqual(parse_qs(urlparse(build_upi_uri(self.aman_share)).query)['am'], ['107.50'])

    @skipUnless(zxingcpp, 'Install requirements-dev.txt to scan generated QR PNGs.')
    def test_browser_cannot_override_amount_or_receiver(self):
        self.client.force_login(self.aman)
        response = self.client.get(reverse('payments:qr', args=[self.aman_share.pk]), {'am': '0.01', 'pa': 'attacker@bank', 'amount': '999'})
        self.assertEqual(response.status_code, 200)
        decoded = zxingcpp.read_barcode(Image.open(BytesIO(response.content)))
        params = parse_qs(urlparse(decoded.text).query)
        self.assertEqual(params['am'], ['107.50'])
        self.assertEqual(params['pa'], ['collector@bank'])

    def test_draft_or_zero_amount_cannot_generate_payment_uri(self):
        Order.objects.filter(pk=self.order.pk).update(status='DRAFT')
        with self.assertRaises(ValidationError):
            build_upi_uri(self.aman_share)
        Order.objects.filter(pk=self.order.pk).update(status='FINALIZED')
        BillAllocation.objects.filter(pk=self.aman_share.pk).update(amount=0)
        with self.assertRaises(ValidationError):
            build_upi_uri(self.aman_share)

    def test_upi_validation_rejects_urls_whitespace_and_injection(self):
        for value in ['upi://pay?pa=x@y', 'name @bank', 'name@bank&am=1', 'bad', 'name@@bank', '@bank', 'name@', 'x\n@bank']:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                validate_upi_id(value)
        for value in ['user.name@bank', '9876543210@upi', 'member-1@okbank']:
            validate_upi_id(value)

    def test_static_qr_upload_stored_privately_as_normalized_png(self):
        config = self.save_static()
        self.assertTrue(config.static_qr.storage.exists(config.static_qr.name))
        self.assertTrue(Path(config.static_qr.path).is_relative_to(Path(self.temp_media.name)))
        self.assertNotIn('qr.png', config.static_qr.name)
        with self.assertRaises(ValueError):
            _ = config.static_qr.url
        self.assertEqual(PaymentAudit.objects.filter(action='QR_SETTINGS_UPDATED').count(), 1)

    def test_static_jpeg_upload_and_appended_payload_are_reencoded(self):
        form = QRSettingsForm(self.form_data(preferred_method='STATIC'), {'static_qr': uploaded_image('safe.jpg', 'JPEG', trailing=b'<script>evil</script>')}, instance=self.config)
        self.assertTrue(form.is_valid(), form.errors)
        sanitized = form.cleaned_data['static_qr'].read()
        self.assertTrue(sanitized.startswith(b'\x89PNG'))
        self.assertNotIn(b'<script>', sanitized)

    def test_invalid_qr_image_uploads_are_rejected(self):
        for name, content in [('qr.png', b'not an image'), ('qr.svg', b'<svg></svg>'), ('qr.exe', b'MZ'), ('qr.png', b'\x89PNG\r\n\x1a\n')]:
            with self.subTest(name=name):
                form = QRSettingsForm(self.form_data(preferred_method='STATIC'), {'static_qr': SimpleUploadedFile(name, content)}, instance=self.config)
                self.assertFalse(form.is_valid())
                self.assertIn('static_qr', form.errors)

    def test_wrong_extension_oversized_and_tiny_images_rejected(self):
        for upload in [uploaded_image('wrong.jpg', 'PNG'), uploaded_image(size=(32, 32)), uploaded_image(trailing=b'x' * (5 * 1024 * 1024)), uploaded_image(size=(4097, 64))]:
            with self.subTest(name=upload.name, size=upload.size):
                form = QRSettingsForm(self.form_data(preferred_method='STATIC'), {'static_qr': upload}, instance=self.config)
                self.assertFalse(form.is_valid())
                self.assertIn('static_qr', form.errors)

    def test_static_mode_without_upi_is_supported(self):
        form = QRSettingsForm(self.form_data(upi_id='', preferred_method='STATIC'), {'static_qr': uploaded_image()}, instance=self.config)
        self.assertTrue(form.is_valid(), form.errors)
        save_qr_settings(self.order.pk, self.rahul, form.cleaned_data)
        self.client.force_login(self.aman)
        page = self.client.get(reverse('payments:member', args=[self.aman_share.pk]))
        self.assertContains(page, 'manually')
        self.assertContains(page, '107.50')
        self.assertNotContains(page, 'href="upi://')
        submit_payment(self.aman_share.pk, self.aman)
        self.assertEqual(get_payment(self.aman_share).status, Payment.Status.PENDING)

    def test_replace_and_remove_static_qr_clean_up_old_file(self):
        config = self.save_static()
        old_name = config.static_qr.name
        form = QRSettingsForm(self.form_data(preferred_method='STATIC'), {'static_qr': uploaded_image()}, instance=config)
        self.assertTrue(form.is_valid())
        with self.captureOnCommitCallbacks(execute=True):
            config = save_qr_settings(self.order.pk, self.rahul, form.cleaned_data)
        self.assertFalse(config.static_qr.storage.exists(old_name))
        new_name, storage = config.static_qr.name, config.static_qr.storage
        form = QRSettingsForm(self.form_data(upi_id='', preferred_method='NONE', remove_qr='on'), instance=config)
        self.assertTrue(form.is_valid(), form.errors)
        with self.captureOnCommitCallbacks(execute=True):
            config = save_qr_settings(self.order.pk, self.rahul, form.cleaned_data)
        self.assertFalse(config.static_qr)
        self.assertFalse(storage.exists(new_name))

    def test_static_response_is_protected_and_has_manual_amount_warning(self):
        self.save_static()
        self.client.force_login(self.aman)
        response = self.client.get(reverse('payments:qr', args=[self.aman_share.pk]))
        self.assertEqual(response['Content-Type'], 'image/png')
        self.assertIn('no-store', response['Cache-Control'])
        response.close()
        self.assertContains(self.client.get(reverse('payments:member', args=[self.aman_share.pk])), 'may need to enter')
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(reverse('payments:qr', args=[self.aman_share.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse('payments:static_preview', args=[self.order.pk])).status_code, 403)

    def test_qr_generation_download_and_page_open_do_not_verify_payment(self):
        self.client.force_login(self.aman)
        for url in [reverse('payments:member', args=[self.aman_share.pk]), reverse('payments:qr', args=[self.aman_share.pk]), reverse('payments:qr', args=[self.aman_share.pk]) + '?download=1']:
            self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(get_payment(self.aman_share).status, Payment.Status.UNPAID)
        self.assertFalse(PaymentAudit.objects.filter(payment__allocation=self.aman_share).exists())

    def test_complete_manual_verification_workflow_and_audit(self):
        payment = get_payment(self.aman_share)
        self.assertEqual(payment.status, 'UNPAID')
        payment = submit_payment(self.aman_share.pk, self.aman, 'REF123')
        self.assertEqual(payment.status, 'PENDING')
        self.assertEqual(payment.reference, 'REF123')
        payment = review_payment(payment.pk, self.rahul, 'VERIFIED')
        self.assertEqual(payment.status, 'VERIFIED')
        self.assertEqual(payment.reviewed_by, self.rahul)
        events = list(payment.audit_events.order_by('pk').values_list('old_status', 'new_status'))
        self.assertEqual(events, [('UNPAID', 'PENDING'), ('PENDING', 'VERIFIED')])

    def test_rejected_payment_can_be_resubmitted_then_verified(self):
        payment = submit_payment(self.aman_share.pk, self.aman)
        payment = review_payment(payment.pk, self.rahul, 'REJECTED', 'No matching receipt in account')
        self.assertEqual(payment.rejection_reason, 'No matching receipt in account')
        payment = submit_payment(self.aman_share.pk, self.aman, 'CORRECTEDREF')
        self.assertEqual(payment.status, 'PENDING')
        self.assertEqual(payment.rejection_reason, '')
        self.assertIsNone(payment.reviewed_by)
        self.assertEqual(review_payment(payment.pk, self.admin, 'VERIFIED').status, 'VERIFIED')
        self.assertEqual(payment.audit_events.count(), 4)

    def test_duplicate_submission_or_verification_rejected(self):
        payment = submit_payment(self.aman_share.pk, self.aman)
        with self.assertRaises(ValidationError):
            submit_payment(self.aman_share.pk, self.aman)
        review_payment(payment.pk, self.rahul, 'VERIFIED')
        with self.assertRaises(ValidationError):
            review_payment(payment.pk, self.rahul, 'VERIFIED')
        with self.assertRaises(ValidationError):
            submit_payment(self.aman_share.pk, self.aman)
        self.assertEqual(payment.audit_events.count(), 2)

    def test_direct_verification_and_rejection_without_reason_rejected(self):
        payment = get_payment(self.aman_share)
        with self.assertRaises(ValidationError):
            review_payment(payment.pk, self.rahul, 'VERIFIED')
        submit_payment(self.aman_share.pk, self.aman)
        with self.assertRaises(ValidationError):
            review_payment(payment.pk, self.rahul, 'REJECTED')
        with self.assertRaises(ValidationError):
            review_payment(payment.pk, self.rahul, 'UNPAID')

    def test_member_and_outsider_cannot_review_or_manage_receiver(self):
        payment = submit_payment(self.aman_share.pk, self.aman)
        for actor in [self.aman, self.outsider]:
            with self.subTest(actor=actor), self.assertRaises(PermissionDenied):
                review_payment(payment.pk, actor, 'VERIFIED')
            with self.assertRaises(PermissionDenied):
                save_qr_settings(self.order.pk, actor, self.form_data())

    def test_collector_cannot_submit_someone_elses_claim(self):
        with self.assertRaises(PermissionDenied):
            submit_payment(self.aman_share.pk, self.rahul)
        self.client.force_login(self.rahul)
        self.assertEqual(self.client.post(reverse('payments:member', args=[self.aman_share.pk]), {}).status_code, 403)

    def test_collector_identity_not_group_owner_controls_review(self):
        DiningGroup.objects.filter(pk=self.group.pk).update(collector=self.aman)
        payment = submit_payment(self.aman_share.pk, self.aman)
        with self.assertRaises(PermissionDenied):
            review_payment(payment.pk, self.rahul, 'VERIFIED')
        self.assertEqual(review_payment(payment.pk, self.aman, 'VERIFIED').status, 'VERIFIED')

    def test_receiver_cannot_change_when_payment_pending_or_verified(self):
        payment = submit_payment(self.aman_share.pk, self.aman)
        for status in ['PENDING', 'VERIFIED']:
            with self.subTest(status=status), self.assertRaises(ValidationError):
                save_qr_settings(self.order.pk, self.rahul, self.form_data(upi_id='changed@bank'))
            if status == 'PENDING':
                review_payment(payment.pk, self.rahul, 'VERIFIED')
        self.config.refresh_from_db()
        self.assertEqual(self.config.upi_id, 'collector@bank')

    def test_totals_count_only_verified_payments_as_received(self):
        payment = submit_payment(self.aman_share.pk, self.aman)
        totals = payment_totals(self.order)
        self.assertEqual(totals['received'], Decimal('0.00'))
        self.assertEqual(totals['pending_verification'], Decimal('107.50'))
        self.assertEqual(totals['outstanding'], Decimal('283.00'))
        review_payment(payment.pk, self.rahul, 'VERIFIED')
        totals = payment_totals(self.order)
        self.assertEqual(totals['received'], Decimal('107.50'))
        self.assertEqual(totals['outstanding'], Decimal('175.50'))

    def test_authenticated_screens_render_and_owner_only_routes(self):
        self.client.force_login(self.rahul)
        for name in ['collector', 'settings']:
            self.assertEqual(self.client.get(reverse(f'payments:{name}', args=[self.order.pk])).status_code, 200)
        self.client.force_login(self.aman)
        self.assertContains(self.client.get(reverse('payments:member', args=[self.aman_share.pk])), 'Open UPI App')
        self.assertEqual(self.client.get(reverse('payments:member', args=[self.rahul_share.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse('payments:settings', args=[self.order.pk])).status_code, 403)

    def test_http_workflow_and_csrf_protection(self):
        self.client.force_login(self.aman)
        self.assertEqual(self.client.post(reverse('payments:member', args=[self.aman_share.pk]), {'reference': 'TXN'}).status_code, 302)
        payment = get_payment(self.aman_share)
        self.client.force_login(self.rahul)
        url = reverse('payments:review', args=[payment.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        secure_client = Client(enforce_csrf_checks=True)
        secure_client.force_login(self.rahul)
        self.assertEqual(secure_client.post(url, {'decision': 'VERIFIED'}).status_code, 403)
        self.assertEqual(self.client.post(url, {'decision': 'VERIFIED'}).status_code, 302)
        payment.refresh_from_db()
        self.assertEqual(payment.status, 'VERIFIED')

    def test_settings_upload_http_and_unauthorized_upload(self):
        url = reverse('payments:settings', args=[self.order.pk])
        self.client.force_login(self.aman)
        self.assertEqual(self.client.post(url, {**self.form_data(preferred_method='STATIC'), 'static_qr': uploaded_image()}).status_code, 403)
        self.client.force_login(self.rahul)
        self.assertEqual(self.client.post(url, {**self.form_data(preferred_method='STATIC'), 'static_qr': uploaded_image()}).status_code, 302)
        self.config.refresh_from_db()
        self.assertTrue(self.config.static_qr)

    def test_missing_setup_and_disabled_collection_cannot_submit(self):
        self.config.delete()
        with self.assertRaises(ValidationError):
            submit_payment(self.aman_share.pk, self.aman)
        PaymentQRSettings.objects.create(order=self.order, preferred_method='NONE')
        with self.assertRaises(ValidationError):
            submit_payment(self.aman_share.pk, self.aman)

    def test_anonymous_users_redirect_to_login(self):
        for name, pk in [('member', self.aman_share.pk), ('qr', self.aman_share.pk), ('settings', self.order.pk), ('collector', self.order.pk)]:
            self.assertEqual(self.client.get(reverse(f'payments:{name}', args=[pk])).status_code, 302)

    def test_disabled_method_cannot_be_bypassed_with_member_qr_request(self):
        PaymentQRSettings.objects.filter(pk=self.config.pk).update(preferred_method='NONE')
        self.client.force_login(self.aman)
        url = reverse('payments:qr', args=[self.aman_share.pk])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.get(url, {'method': 'DYNAMIC'}).status_code, 404)
        self.client.force_login(self.rahul)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.get(url, {'method': 'DYNAMIC'}).status_code, 200)

    def test_pending_verified_and_zero_pages_do_not_prompt_duplicate_payment(self):
        self.client.force_login(self.aman)
        payment = submit_payment(self.aman_share.pk, self.aman)
        url = reverse('payments:member', args=[self.aman_share.pk])
        response = self.client.get(url)
        self.assertContains(response, 'Do not pay again')
        self.assertNotContains(response, 'href="upi://')
        review_payment(payment.pk, self.rahul, 'VERIFIED')
        response = self.client.get(url)
        self.assertContains(response, 'No further payment needed')
        self.assertNotContains(response, 'Three steps to settle up')
        BillAllocation.objects.filter(pk=self.rahul_share.pk).update(amount='0.00')
        self.client.force_login(self.rahul)
        response = self.client.get(reverse('payments:member', args=[self.rahul_share.pk]))
        self.assertContains(response, 'No payment due')
        self.assertNotContains(response, 'Three steps to settle up')
