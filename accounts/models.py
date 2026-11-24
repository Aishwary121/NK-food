from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models


class Profile(models.Model):
    """Optional contact details; authentication remains Django's responsibility."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    mobile = models.CharField(max_length=16, blank=True, validators=[RegexValidator(r"^\+?[0-9]{10,15}$", "Enter 10–15 digits, optionally starting with +.")])

    def __str__(self):
        return self.user.get_full_name() or self.user.username


class FirebaseIdentity(models.Model):
    """A verified Firebase identity; email addresses are never linking keys."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='firebase_identity')
    project_id = models.CharField(max_length=128)
    uid = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['project_id', 'uid'], name='unique_firebase_project_uid')]

    def __str__(self):
        return f'Google account for {self.user.username}'

