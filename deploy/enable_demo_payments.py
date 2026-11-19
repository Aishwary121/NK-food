"""Back up the hosted ledger and install demo payments without settling any bill."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import sys


def main():
    if len(sys.argv) != 2:
        raise SystemExit('Usage: python deploy/enable_demo_payments.py /private/environment.json')
    configuration = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    os.environ.update({key: str(value) for key, value in configuration.items()})
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'nk_foodcourt.settings')
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import django
    django.setup()
    from django.conf import settings
    from django.core.management import call_command
    from orders.models import BillAllocation
    from payments.models import CounterPayment, Payment

    if not settings.PAYMENT_DEMO_MODE:
        raise SystemExit('PAYMENT_DEMO_MODE must be enabled for this demo deployment.')
    database = settings.DATABASES['default']
    if database['ENGINE'] != 'django.db.backends.sqlite3':
        raise SystemExit('Use the database provider backup procedure for non-SQLite deployments.')
    source = Path(database['NAME']).resolve()
    backups = source.parent / 'backups'
    backups.mkdir(mode=0o700, exist_ok=True)
    backup = backups / ('before-demo-payments-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.sqlite3')
    with sqlite3.connect(str(source)) as reader, sqlite3.connect(str(backup)) as writer:
        reader.backup(writer)
    backup.chmod(0o600)
    amounts = dict(BillAllocation.objects.values_list('pk', 'amount'))
    statuses = dict(Payment.objects.values_list('pk', 'status'))
    counter_amounts = dict(CounterPayment.objects.values_list('pk', 'amount'))
    call_command('migrate', interactive=False)
    assert amounts == dict(BillAllocation.objects.filter(pk__in=amounts).values_list('pk', 'amount')), 'Allocation preservation failed'
    assert statuses == dict(Payment.objects.filter(pk__in=statuses).values_list('pk', 'status')), 'Payment preservation failed'
    assert counter_amounts == dict(CounterPayment.objects.filter(pk__in=counter_amounts).values_list('pk', 'amount')), 'Counter preservation failed'
    call_command('collectstatic', interactive=False, verbosity=0)
    call_command('check', deploy=True)
    print(json.dumps({'result': 'PASS', 'demo_mode': True, 'private_backup_created': True,
                      'existing_allocations_preserved': len(amounts), 'existing_payments_preserved': len(statuses),
                      'payments_simulated_by_deployment': 0}))


if __name__ == '__main__':
    main()
