from django.conf import settings


def payment_mode(request):
    return {'payment_demo_mode': settings.PAYMENT_DEMO_MODE}
