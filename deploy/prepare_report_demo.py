"""Opt-in synthetic report fixtures for an already configured deployment.

Run: python deploy/prepare_report_demo.py ~/.nk-food-environment.json
Enter a randomly generated master secret of at least 32 characters at the hidden
prompt. It is never written or printed by this helper. Each login password is:
HMAC-SHA256(master UTF-8 bytes, ASCII 'NK_FOODCOURT_REPORT_V1:' + username).hexdigest()
The five usernames are admin, rahul, aman, rohit and karan. Passwords have 64 hex
characters. Retain the master only in the operator's private secret storage.

The helper aborts without changes if ANY of those usernames already exists
(case-insensitive). It creates synthetic groups, bills and unpaid payment rows;
it never records a bank transfer or marks a payment pending/verified/rejected.
The fictional QR recipient is report-demo@example, labelled Do Not Pay.
Output is only a JSON object of created group/order/allocation IDs. Normal
deployment and menu seeding do not invoke this explicitly opt-in helper.
"""
import argparse
import getpass
import hashlib
import hmac
from io import StringIO
import json
import os
from pathlib import Path
import sys
import warnings


USERNAMES = ('admin', 'rahul', 'aman', 'rohit', 'karan')


def derive_password(master, username):
    return hmac.new(
        master.encode('utf-8'),
        ('NK_FOODCOURT_REPORT_V1:' + username).encode('ascii'),
        hashlib.sha256,
    ).hexdigest()


def create_demo(master):
    """Django must be configured before calling; one all-or-nothing DB transaction."""
    from django.contrib.auth import get_user_model
    from django.core.exceptions import ValidationError
    from django.core.files.uploadedfile import SimpleUploadedFile
    from django.db import transaction
    from django.db.models import Q
    from accounts.models import Profile
    from menu.management.commands.seed_data import Command
    from menu.models import MenuItem
    from orders.models import Order
    from orders.services import add_to_cart
    from payments.forms import QRSettingsForm
    from payments.models import Payment
    from payments.services import generate_qr_png, get_payment, save_qr_settings

    if len(master) < 32:
        raise ValueError('Use a randomly generated master secret of at least 32 characters.')
    User = get_user_model()
    matches = Q()
    for username in USERNAMES:
        matches |= Q(username__iexact=username)
    with transaction.atomic():
        if User.objects.filter(matches).exists():
            raise ValueError('Aborted: at least one report username already exists. No existing account was changed.')
        users = {}
        for username in USERNAMES:
            users[username] = User.objects.create_user(
                username=username,
                password=derive_password(master, username),
                first_name='Report Administrator' if username == 'admin' else username.title(),
                email=f'{username}@example.com',
                is_staff=username == 'admin',
                is_superuser=username == 'admin',
            )
            Profile.objects.get_or_create(user=users[username])

        # This method creates only groups/orders via normal services. Do not call
        # Command.handle(): its DEBUG-only demo branch uses public demo passwords.
        Command(stdout=StringIO()).seed_demo_order()
        final_order = Order.objects.get(group__owner=users['rahul'], status='FINALIZED')
        draft_order = Order.objects.get(group__owner=users['rahul'], status='DRAFT')
        for name, quantity in [('Tawa Roti', 4), ('Shahi Paneer', 1), ('Cold Coffee', 1)]:
            add_to_cart(
                group_id=draft_order.group_id, actor=users['rahul'],
                menu_item=MenuItem.objects.get(name=name), quantity=quantity,
            )

        expected = {'rahul': '175.50', 'aman': '107.50', 'rohit': '73.50', 'karan': '80.50'}
        allocations = list(final_order.allocations.select_related('member__user'))
        actual = {allocation.member.user.username: format(allocation.amount, '.2f') for allocation in allocations}
        if actual != expected or format(final_order.total, '.2f') != '437.00':
            raise ValueError('Aborted: deployed menu prices do not produce the expected report bill. No records were retained.')
        for allocation in allocations:
            payment = get_payment(allocation)
            if payment.status != Payment.Status.UNPAID:
                raise ValueError('Aborted: a report payment was unexpectedly not unpaid.')

        static_uri = 'upi://pay?pa=report-demo@example&pn=Report%20Demo%20-%20Do%20Not%20Pay&cu=INR&tn=DEMONSTRATION_ONLY'
        form = QRSettingsForm(
            {'upi_id': 'report-demo@example', 'display_name': 'Report Demo - Do Not Pay', 'preferred_method': 'DYNAMIC'},
            {'static_qr': SimpleUploadedFile('report-example.png', generate_qr_png(static_uri), content_type='image/png')},
        )
        if not form.is_valid():
            raise ValidationError('The synthetic static QR failed the normal upload validation.')
        save_qr_settings(final_order.pk, users['rahul'], form.cleaned_data)

        return {
            'order': final_order.pk,
            'draft': draft_order.pk,
            'group': final_order.group_id,
            'draft_group': draft_order.group_id,
            'allocations': {allocation.member.user.username: allocation.pk for allocation in allocations},
        }


def main():
    parser = argparse.ArgumentParser(description='Create protected synthetic report fixtures with unique passwords.')
    parser.add_argument('environment_file', type=Path, help='Private deployment environment JSON file; no secret values on the command line.')
    args = parser.parse_args()
    configuration = json.loads(args.environment_file.expanduser().read_text(encoding='utf-8'))
    os.environ.update({key: str(value) for key, value in configuration.items()})
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    os.environ['DJANGO_SETTINGS_MODULE'] = 'nk_foodcourt.settings'
    import django
    django.setup()

    # Reject getpass's echoing fallback, rather than exposing a master secret.
    with warnings.catch_warnings():
        warnings.simplefilter('error', getpass.GetPassWarning)
        master = getpass.getpass('Report master secret (hidden; random, at least 32 characters): ')
    result = create_demo(master)
    print(json.dumps(result, separators=(',', ':')))


if __name__ == '__main__':
    main()
