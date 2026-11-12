from django.contrib import admin
from .models import DiningGroup, Membership, Pair


class ReadOnlyGroupAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(DiningGroup)
class DiningGroupAdmin(ReadOnlyGroupAdmin):
    list_display = ['name', 'code', 'owner', 'collector', 'status', 'created_at']
    list_filter = ['status']
    search_fields = ['name', 'code']


admin.site.register(Membership, ReadOnlyGroupAdmin)
admin.site.register(Pair, ReadOnlyGroupAdmin)
