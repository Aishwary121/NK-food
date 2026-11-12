from django.conf import settings
from django.db import models
from .storage import private_qr_storage, qr_upload_path
from .validators import validate_upi_id

class PaymentQRSettings(models.Model):
    class Method(models.TextChoices):
        DYNAMIC = 'DYNAMIC', 'Amount-specific UPI QR'
        STATIC = 'STATIC', 'Uploaded UPI QR'
        NONE = 'NONE', 'Payment collection not configured'

    order = models.OneToOneField('orders.Order', on_delete=models.PROTECT, related_name='payment_settings')
    upi_id = models.CharField(max_length=193, blank=True, validators=[validate_upi_id])
    display_name = models.CharField(max_length=80, blank=True)
    preferred_method = models.CharField(max_length=10, choices=Method.choices, default=Method.DYNAMIC)
    static_qr = models.FileField(storage=private_qr_storage, upload_to=qr_upload_path, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'QR settings: {self.order.number}'

class Payment(models.Model):
    class Status(models.TextChoices):
        UNPAID = 'UNPAID', 'Unpaid'
        PENDING = 'PENDING', 'Pending verification'
        VERIFIED = 'VERIFIED', 'Verified'
        REJECTED = 'REJECTED', 'Rejected'

    # Amounts live exclusively on immutable finalized BillAllocation records.
    allocation = models.OneToOneField('orders.BillAllocation', on_delete=models.PROTECT, related_name='payment')
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.UNPAID)
    reference = models.CharField(max_length=80, blank=True)
    rejection_reason = models.CharField(max_length=300, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='reviewed_payments')

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(status__in=['UNPAID', 'PENDING', 'VERIFIED', 'REJECTED']), name='payment_valid_status')]

    def __str__(self):
        return f'{self.allocation} — {self.get_status_display()}'

class PaymentAudit(models.Model):
    """Append-only application audit trail. The admin exposes it read-only."""
    order = models.ForeignKey('orders.Order', on_delete=models.PROTECT, related_name='payment_audit')
    payment = models.ForeignKey(Payment, null=True, blank=True, on_delete=models.PROTECT, related_name='audit_events')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    action = models.CharField(max_length=40)
    old_status = models.CharField(max_length=10, blank=True)
    new_status = models.CharField(max_length=10, blank=True)
    detail = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-pk']

    def __str__(self):
        return f'{self.action} — {self.created_at}'
