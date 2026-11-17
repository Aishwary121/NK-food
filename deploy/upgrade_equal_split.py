"""Back up the hosted SQLite ledger, then enable equal draft bills safely.

Run with the production virtualenv and private environment JSON path. No
credentials or individual customer details are printed. Historical finalized
amounts are compared before and after migration; no payment is declared here.
"""
import json
import os
from pathlib import Path
import sqlite3
import sys
from datetime import datetime, timezone
from decimal import Decimal


def main():
    if len(sys.argv) != 2:
        raise SystemExit('Usage: python deploy/upgrade_equal_split.py /private/environment.json')
    configuration = json.loads(Path(sys.argv[1]).expanduser().read_text(encoding='utf-8'))
    os.environ.update({key: str(value) for key, value in configuration.items()})
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'nk_foodcourt.settings')
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import django
    django.setup()
    from django.conf import settings
    from django.core.management import call_command
    from orders.models import BillAllocation, Order
    from orders.services import bill_breakdown

    database = settings.DATABASES['default']
    if database['ENGINE'] != 'django.db.backends.sqlite3':
        raise SystemExit('This deployment helper expects SQLite; use the database provider backup procedure otherwise.')
    source = Path(database['NAME']).resolve()
    backups = source.parent / 'backups'
    backups.mkdir(mode=0o700, exist_ok=True)
    backup = backups / ('before-equal-split-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.sqlite3')
    with sqlite3.connect(str(source)) as reader, sqlite3.connect(str(backup)) as writer:
        reader.backup(writer)
    backup.chmod(0o600)
    frozen = dict(BillAllocation.objects.filter(order__status='FINALIZED').values_list('pk', 'amount'))
    call_command('migrate', interactive=False)
    retained = dict(BillAllocation.objects.filter(pk__in=frozen).values_list('pk', 'amount'))
    if frozen != retained:
        raise SystemExit('Historical bill verification failed. Stop before reloading; a private database backup is available.')
    checked = 0
    for order in Order.objects.filter(status='DRAFT', split_policy='EQUAL').select_related('group'):
        shares = [row['amount'] for row in bill_breakdown(order)]
        if not shares or sum(shares) != order.total or max(shares) - min(shares) > Decimal('0.01'):
            raise SystemExit('Draft equal-split verification failed. Stop before reloading.')
        if order.group.owner_id != order.group.collector_id:
            raise SystemExit('Draft host/collector verification failed. Stop before reloading.')
        checked += 1
    call_command('collectstatic', interactive=False, verbosity=0)
    call_command('check', deploy=True)
    print(json.dumps({'result': 'PASS', 'private_backup_created': True,
                      'historical_allocations_preserved': len(frozen), 'equal_drafts_checked': checked,
                      'payments_declared_or_verified': 0}))


if __name__ == '__main__':
    main()
