from django.contrib.auth import views as auth_views
from django.urls import path, reverse_lazy

from . import views
from .login import SignInView

app_name = "accounts"
urlpatterns = [
    path("register/", views.register, name="register"),
    path("login/", SignInView.as_view(), name="login"),
    path("google/sign-in/", views.google_sign_in, name="google_sign_in"),
    path("logout/", auth_views.LogoutView.as_view(next_page=reverse_lazy("menu:home")), name="logout"),
    path("profile/", views.profile, name="profile"),
    path("profile/edit/", views.profile_edit, name="profile_edit"),
    path("password/change/", auth_views.PasswordChangeView.as_view(template_name="registration/password_change_form.html", success_url=reverse_lazy("accounts:password_change_done")), name="password_change"),
    path("password/changed/", auth_views.PasswordChangeDoneView.as_view(template_name="registration/password_change_done.html"), name="password_change_done"),
]

