import os
import uuid
from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible

@deconstructible
class PrivateQRStorage(FileSystemStorage):
    """Only authenticated Django FileResponse views can expose these files."""
    @property
    def base_location(self):
        return settings.PRIVATE_MEDIA_ROOT

    @property
    def location(self):
        return os.path.abspath(self.base_location)

    def url(self, name):
        raise ValueError('Payment QR images must be accessed through an authorized payment view.')

def qr_upload_path(instance, filename):
    return f'payment_qr/{uuid.uuid4().hex}.png'

private_qr_storage = PrivateQRStorage()
