from django.conf import settings
from django.contrib.auth.views import LoginView


class SignInView(LoginView):
    template_name = 'registration/login.html'

    def render_to_response(self, context, **response_kwargs):
        response = super().render_to_response(context, **response_kwargs)
        if settings.FIREBASE_WEB_CONFIG:
            # Keep the Google auth popup connected to this sign-in page.
            response['Cross-Origin-Opener-Policy'] = 'same-origin-allow-popups'
        return response
