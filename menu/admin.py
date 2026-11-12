from django.contrib import admin

from .models import Category, MenuItem


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "sort_order")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(MenuItem)
class MenuItemAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "price", "is_available", "is_featured", "availability_end")
    list_editable = ("price", "is_available", "is_featured")
    list_filter = ("category", "is_available", "is_vegetarian", "is_featured")
    search_fields = ("name", "description")

