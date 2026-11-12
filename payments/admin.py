from django.contrib import admin
from .models import Payment, PaymentAudit, PaymentQRSettings

class LedgerReadOnlyAdmin(admin.ModelAdmin):
    """Administrators use the collector dashboard, so reviews always create audit events."""
    actions = None

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

@admin.register(Payment)
class PaymentAdmin(LedgerReadOnlyAdmin):
    list_display = ['id', 'allocation', 'status', 'submitted_at', 'reviewed_by', 'reviewed_at']
    list_filter = ['status']

@admin.register(PaymentAudit)
class AuditAdmin(LedgerReadOnlyAdmin):
    list_display = ['created_at', 'order', 'actor', 'action', 'old_status', 'new_status']
    list_filter = ['action', 'new_status']

@admin.register(PaymentQRSettings)
class SettingsAdmin(LedgerReadOnlyAdmin):
    list_display = ['order', 'display_name', 'preferred_method', 'updated_at']
