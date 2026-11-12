from django.contrib import admin
from .models import AllocationAudit, BillAllocation, CartItem, ItemAllocation, Order, OrderItem


class ReadOnlyOrderAdmin(admin.ModelAdmin):
    """Use validated services, not raw admin edits, for financial state changes."""
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Order)
class OrderAdmin(ReadOnlyOrderAdmin):
    list_display = ['number', 'group', 'status', 'total', 'created_at']
    list_filter = ['status']
    search_fields = ['number', 'group__name']


admin.site.register(OrderItem, ReadOnlyOrderAdmin)
admin.site.register(ItemAllocation, ReadOnlyOrderAdmin)
admin.site.register(BillAllocation, ReadOnlyOrderAdmin)
admin.site.register(AllocationAudit, ReadOnlyOrderAdmin)
admin.site.register(CartItem, ReadOnlyOrderAdmin)
