from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(unique=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("sort_order", "name")
        verbose_name_plural = "categories"

    def __str__(self):
        return self.name


class MenuItem(models.Model):
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="items")
    name = models.CharField(max_length=160)
    description = models.CharField(max_length=300, blank=True)
    price = models.DecimalField(max_digits=9, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(Decimal("0.01"))], help_text="Leave blank for MRP items until the current price is entered.")
    image = models.ImageField(upload_to="menu/", blank=True)
    is_available = models.BooleanField(default=True)
    is_vegetarian = models.BooleanField(default=True)
    is_featured = models.BooleanField(default=False)
    availability_start = models.TimeField(null=True, blank=True)
    availability_end = models.TimeField(null=True, blank=True)

    class Meta:
        ordering = ("category__sort_order", "name")
        constraints = [models.UniqueConstraint(fields=("category", "name"), name="menu_unique_dish_in_category"), models.CheckConstraint(condition=models.Q(price__gt=0) | models.Q(price__isnull=True), name="menu_price_positive_or_unknown")]

    @property
    def available_now(self):
        if not self.is_available or self.price is None:
            return False
        now = timezone.localtime().time()
        start, end = self.availability_start, self.availability_end
        if start and end and start > end:
            return now >= start or now < end
        return (start is None or now >= start) and (end is None or now < end)

    @property
    def availability_note(self):
        if not self.is_available:
            return "Currently unavailable"
        if self.price is None:
            return "Ask the counter for today's MRP"
        if self.availability_end:
            return f"Available until {self.availability_end.strftime('%I:%M %p').lstrip('0')}"
        return "Freshly prepared"

    @property
    def stock_photo(self):
        from .photos import stock_photo
        return stock_photo(self.name)

    @property
    def photo_url(self):
        if self.image:
            return self.image.url
        photo = self.stock_photo
        return photo['url'] if photo else ''

    def __str__(self):
        return self.name

