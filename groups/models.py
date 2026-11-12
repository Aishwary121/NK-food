import secrets
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


def group_code():
    return secrets.token_hex(4).upper()


class DiningGroup(models.Model):
    class Status(models.TextChoices):
        OPEN = 'OPEN', 'Open'
        ORDERING = 'ORDERING', 'Ordering'
        FINALIZED = 'FINALIZED', 'Finalized'
        COMPLETED = 'COMPLETED', 'Completed'
        CANCELLED = 'CANCELLED', 'Cancelled'

    name = models.CharField(max_length=120)
    code = models.CharField(max_length=8, unique=True, default=group_code, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='owned_groups')
    collector = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='collected_groups')
    description = models.TextField(blank=True, max_length=1000)
    max_members = models.PositiveSmallIntegerField(default=4, validators=[MinValueValidator(1), MaxValueValidator(50)])
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.OPEN)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    @property
    def is_open(self):
        return self.status in (self.Status.OPEN, self.Status.ORDERING)

    def __str__(self):
        return self.name


class Pair(models.Model):
    group = models.ForeignKey(DiningGroup, on_delete=models.CASCADE, related_name='pairs')
    name = models.CharField(max_length=80)

    def __str__(self):
        return self.name


class Membership(models.Model):
    group = models.ForeignKey(DiningGroup, on_delete=models.CASCADE, related_name='memberships')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='dining_memberships')
    # One nullable pair per membership makes duplicate active pair membership impossible.
    pair = models.ForeignKey(Pair, on_delete=models.SET_NULL, null=True, blank=True, related_name='memberships')
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['group', 'user'], name='unique_group_member')]
        ordering = ['pk']

    @property
    def display_name(self):
        return self.user.get_full_name() or self.user.username

    def clean(self):
        if self.pair_id and self.pair.group_id != self.group_id:
            raise ValidationError('A pair must belong to the same dining group.')

    def __str__(self):
        return self.display_name
