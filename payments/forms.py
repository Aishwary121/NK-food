import warnings
from io import BytesIO
from pathlib import Path
from PIL import Image, ImageOps, UnidentifiedImageError
from django import forms
from django.core.files.base import ContentFile
from .models import PaymentQRSettings
from .validators import validate_upi_id

MAX_QR_BYTES = 5 * 1024 * 1024
ALLOWED_FORMATS = {'.png': 'PNG', '.jpg': 'JPEG', '.jpeg': 'JPEG', '.webp': 'WEBP'}

class QRSettingsForm(forms.Form):
    upi_id = forms.CharField(max_length=193, required=False, label='Collector UPI ID', help_text='For example: yourname@bank. Never enter a UPI PIN.', validators=[validate_upi_id])
    display_name = forms.CharField(max_length=80, label='UPI account display name')
    preferred_method = forms.ChoiceField(choices=PaymentQRSettings.Method.choices, label='Preferred payment method')
    static_qr = forms.FileField(required=False, label='Upload or replace QR image', help_text='PNG, JPEG or WebP, up to 5 MB. Use a clear image of your own UPI QR.', widget=forms.FileInput(attrs={'accept': '.png,.jpg,.jpeg,.webp'}))
    remove_qr = forms.BooleanField(required=False, label='Remove uploaded QR image')

    def __init__(self, *args, instance=None, **kwargs):
        self.instance = instance
        if instance and not args:
            kwargs.setdefault('initial', {key: getattr(instance, key) for key in ['upi_id', 'display_name', 'preferred_method']})
        super().__init__(*args, **kwargs)

    def clean_display_name(self):
        value = self.cleaned_data['display_name'].strip()
        if any(ord(c) < 32 for c in value):
            raise forms.ValidationError('Use a readable account display name.')
        return value

    def clean_static_qr(self):
        upload = self.cleaned_data.get('static_qr')
        if not upload:
            return None
        extension = Path(upload.name).suffix.lower()
        if extension not in ALLOWED_FORMATS or upload.size > MAX_QR_BYTES:
            raise forms.ValidationError('Upload a PNG, JPEG or WebP image no larger than 5 MB.')
        try:
            # Check real image content; file extensions and browser MIME types are untrusted.
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                img = Image.open(upload)
                if img.format != ALLOWED_FORMATS[extension]:
                    raise ValueError('Image type does not match its extension.')
                if min(img.size) < 64 or max(img.size) > 4096 or img.width * img.height > 16_000_000:
                    raise ValueError('Use an image between 64 and 4096 pixels per side (at most 16 megapixels).')
                if getattr(img, 'n_frames', 1) != 1:
                    raise ValueError('Animated images are not supported.')
                img.verify()
                upload.seek(0)
                with Image.open(upload) as decoded:
                    decoded.load()
                    oriented = ImageOps.exif_transpose(decoded).convert('RGBA')
                    clean = Image.new('RGB', oriented.size, 'white')
                    clean.paste(oriented, mask=oriented.getchannel('A'))
                    output = BytesIO()
                    # Re-encode pixels to strip filenames, metadata, and appended payloads.
                    clean.save(output, format='PNG')
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise forms.ValidationError('Upload a valid, non-animated PNG, JPEG or WebP image within the pixel limits.') from exc
        if output.tell() > MAX_QR_BYTES:
            raise forms.ValidationError('The normalized QR image must be smaller than 5 MB.')
        return ContentFile(output.getvalue(), name='qr.png')

    def clean(self):
        data = super().clean()
        if data.get('remove_qr') and data.get('static_qr'):
            self.add_error('remove_qr', 'Choose either replacement or removal.')
        has_static = data.get('static_qr') or (self.instance and self.instance.static_qr and not data.get('remove_qr'))
        if data.get('preferred_method') == PaymentQRSettings.Method.DYNAMIC and not data.get('upi_id'):
            self.add_error('upi_id', 'Enter a UPI ID to generate amount-specific QR codes.')
        if data.get('preferred_method') == PaymentQRSettings.Method.STATIC and not has_static:
            self.add_error('static_qr', 'Upload a QR image before selecting this method.')
        return data

class PaymentClaimForm(forms.Form):
    reference = forms.CharField(max_length=80, required=False, label='UPI transaction reference (optional)', help_text='Share only the transaction reference. Do not enter a UPI PIN or banking credentials.')


class CounterPaymentForm(forms.Form):
    reference = forms.CharField(max_length=80, required=False, label='Counter receipt or transaction reference (optional)')
    confirm = forms.BooleanField(label='I confirm that the full bill has been paid at the restaurant counter.', help_text='Record this only after paying. This is a declaration, not an automatic bank confirmation.')

class PaymentReviewForm(forms.Form):
    decision = forms.ChoiceField(choices=[('VERIFIED', 'Verify'), ('REJECTED', 'Reject')])
    reason = forms.CharField(max_length=300, required=False)

    def clean(self):
        data = super().clean()
        if data.get('decision') == 'REJECTED' and not data.get('reason'):
            self.add_error('reason', 'Explain why this payment was rejected.')
        return data
