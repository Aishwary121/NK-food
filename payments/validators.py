import re
from django.core.exceptions import ValidationError

UPI_PATTERN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{1,127}@[A-Za-z][A-Za-z0-9.-]{1,63}$')

def validate_upi_id(value):
    """Syntax validation only: account existence is confirmed in the payer's UPI app."""
    if not UPI_PATTERN.fullmatch(value or ''):
        raise ValidationError('Enter a valid UPI ID, such as yourname@bank. Spaces and payment URLs are not allowed.')
