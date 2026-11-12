from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm
from django.db import transaction

from .models import Profile

User = get_user_model()


class RegistrationForm(UserCreationForm):
    full_name = forms.CharField(max_length=150, label="Full name")
    email = forms.EmailField()
    mobile = forms.CharField(max_length=16, validators=Profile._meta.get_field("mobile").validators)

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("full_name", "username", "email", "mobile", "password1", "password2")

    @transaction.atomic
    def save(self, commit=True):
        user = super().save(commit=False)
        parts = self.cleaned_data["full_name"].strip().split(" ", 1)
        user.first_name = parts[0]
        user.last_name = parts[1] if len(parts) > 1 else ""
        user.email = self.cleaned_data["email"]
        if commit:
            user.save()
            Profile.objects.create(user=user, mobile=self.cleaned_data["mobile"])
        return user


class ProfileForm(forms.ModelForm):
    mobile = forms.CharField(max_length=16, required=False, validators=Profile._meta.get_field("mobile").validators)

    class Meta:
        model = User
        fields = ("first_name", "last_name", "email", "mobile")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            profile = Profile.objects.filter(user=self.instance).first()
            self.fields["mobile"].initial = profile.mobile if profile else ""

    @transaction.atomic
    def save(self, commit=True):
        user = super().save(commit=commit)
        if commit:
            Profile.objects.update_or_create(user=user, defaults={"mobile": self.cleaned_data["mobile"]})
        return user

