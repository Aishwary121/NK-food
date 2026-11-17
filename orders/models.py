from decimal import Decimal
import uuid
from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


def order_number():
    return 'NK-' + uuid.uuid4().hex[:12].upper()


class Order(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'In progress'
        FINALIZED = 'FINALIZED', 'Finalized'

    class SplitPolicy(models.TextChoices):
        EQUAL = 'EQUAL', 'Equal across all members'
        ITEMIZED = 'ITEMIZED', 'Original item-based allocation'

    group = models.OneToOneField('groups.DiningGroup', on_delete=models.PROTECT, related_name='order')
    number = models.CharField(max_length=24, unique=True, default=order_number, editable=False)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    split_policy = models.CharField(max_length=10, choices=SplitPolicy.choices, default=SplitPolicy.EQUAL, editable=False)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax = models.DecimalField(max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    discount = models.DecimalField(max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    finalized_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [models.CheckConstraint(condition=models.Q(total__gte=0), name='nonnegative_order_total')]

    def __str__(self):
        return self.number


class CartItem(models.Model):
    member = models.ForeignKey('groups.Membership', on_delete=models.CASCADE, related_name='cart_items')
    menu_item = models.ForeignKey('menu.MenuItem', on_delete=models.PROTECT)
    quantity = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1), MaxValueValidator(999)])

    class Meta:
        constraints = [models.UniqueConstraint(fields=['member', 'menu_item'], name='unique_personal_cart_item')]

    @property
    def subtotal(self):
        return (self.menu_item.price or Decimal('0')) * self.quantity


class OrderItem(models.Model):
    class SplitMode(models.TextChoices):
        EQUAL = 'EQUAL', 'Share equally'
        QUANTITY = 'QUANTITY', 'By quantity'
        CUSTOM = 'CUSTOM', 'Custom amounts'
        PERCENT = 'PERCENT', 'By percentage'
        INDIVIDUAL = 'INDIVIDUAL', 'One person'

    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name='items')
    menu_item = models.ForeignKey('menu.MenuItem', on_delete=models.PROTECT)
    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    # Snapshot price/name: later menu changes must not alter a finalized bill.
    item_name = models.CharField(max_length=200)
    quantity = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(999)])
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    split_mode = models.CharField(max_length=12, choices=SplitMode.choices, default=SplitMode.INDIVIDUAL)

    class Meta:
        ordering = ['pk']

    @property
    def subtotal(self):
        return self.unit_price * self.quantity


class ItemAllocation(models.Model):
    order_item = models.ForeignKey(OrderItem, on_delete=models.CASCADE, related_name='allocations')
    member = models.ForeignKey('groups.Membership', on_delete=models.PROTECT, related_name='item_allocations')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    quantity = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['order_item', 'member'], name='unique_item_allocation'),
            models.CheckConstraint(condition=models.Q(amount__gte=0), name='nonnegative_item_allocation'),
        ]


class BillAllocation(models.Model):
    """Immutable final amount consumed by the payment module, never client input."""

    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name='allocations')
    member = models.ForeignKey('groups.Membership', on_delete=models.PROTECT, related_name='bill_allocations')
    amount = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        ordering = ['member_id']
        constraints = [
            models.UniqueConstraint(fields=['order', 'member'], name='unique_bill_allocation'),
            models.CheckConstraint(condition=models.Q(amount__gte=0), name='nonnegative_bill_allocation'),
        ]


class AllocationAudit(models.Model):
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name='allocation_history')
    item = models.ForeignKey(OrderItem, null=True, on_delete=models.SET_NULL)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    action = models.CharField(max_length=30)
    before = models.JSONField(default=dict)
    after = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-pk']
