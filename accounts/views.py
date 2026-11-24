import json

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from django.views.decorators.http import require_POST

from .firebase_auth import FirebaseSignInUnavailable, InvalidFirebaseToken, MAX_TOKEN_LENGTH, user_for_firebase_identity, verify_firebase_token
from .forms import ProfileForm, RegistrationForm


def register(request):
    if request.user.is_authenticated:
        return redirect("menu:home")
    form = RegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        messages.success(request, "Your table is waiting. Welcome to N.K. Food Court!")
        return redirect("menu:home")
    response = render(request, "accounts/register.html", {"form": form})
    if getattr(settings, 'FIREBASE_WEB_CONFIG', {}):
        response['Cross-Origin-Opener-Policy'] = 'same-origin-allow-popups'
    return response


def _google_redirect(request, destination):
    if (isinstance(destination, str) and len(destination) <= 2048
            and destination.startswith('/') and not destination.startswith('//')
            and '\\' not in destination and not any(ord(char) < 32 for char in destination)
            and url_has_allowed_host_and_scheme(destination, allowed_hosts={request.get_host()}, require_https=request.is_secure())):
        return destination
    return reverse('menu:home')


@never_cache
@require_POST
@csrf_protect
@sensitive_post_parameters('id_token')
@sensitive_variables('payload', 'id_token', 'claims')
def google_sign_in(request):
    if request.content_type != 'application/json':
        return JsonResponse({'error': 'Send Google sign-in as a JSON request.'}, status=400)
    try:
        content_length = int(request.META.get('CONTENT_LENGTH') or 0)
        if content_length > MAX_TOKEN_LENGTH + 4096 or len(request.body) > MAX_TOKEN_LENGTH + 4096:
            raise ValueError
        payload = json.loads(request.body)
        if not isinstance(payload, dict):
            raise ValueError
        id_token = payload.get('id_token')
        if not isinstance(id_token, str) or not 0 < len(id_token) <= MAX_TOKEN_LENGTH:
            raise ValueError
    except (ValueError, TypeError, UnicodeError):
        return JsonResponse({'error': 'A valid Google sign-in request is required.'}, status=400)
    try:
        claims = verify_firebase_token(id_token)
        user = user_for_firebase_identity(claims)
    except FirebaseSignInUnavailable as exc:
        return JsonResponse({'error': str(exc)}, status=503)
    except InvalidFirebaseToken as exc:
        return JsonResponse({'error': str(exc)}, status=401)
    login(request, user, backend='django.contrib.auth.backends.ModelBackend')
    return JsonResponse({'redirect': _google_redirect(request, payload.get('next'))})


@login_required
def profile(request):
    return render(request, "accounts/profile.html")


@login_required
def profile_edit(request):
    form = ProfileForm(request.POST or None, instance=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Your profile has been updated.")
        return redirect("accounts:profile")
    return render(request, "accounts/profile_edit.html", {"form": form})

