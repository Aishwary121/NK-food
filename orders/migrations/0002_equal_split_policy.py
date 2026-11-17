from django.db import migrations, models


def enable_equal_drafts(apps, schema_editor):
    Order = apps.get_model('orders', 'Order')
    DiningGroup = apps.get_model('groups', 'DiningGroup')
    AllocationAudit = apps.get_model('orders', 'AllocationAudit')
    PaymentAudit = apps.get_model('payments', 'PaymentAudit')
    PaymentQRSettings = apps.get_model('payments', 'PaymentQRSettings')
    alias = schema_editor.connection.alias
    for order in Order.objects.using(alias).filter(status='DRAFT').select_related('group').iterator():
        # Existing final bills and payment claims must never be recalculated.
        Order.objects.using(alias).filter(pk=order.pk).update(split_policy='EQUAL')
        AllocationAudit.objects.using(alias).create(
            order_id=order.pk, actor_id=order.group.owner_id, action='SPLIT_POLICY_CHANGED',
            before={'split_policy': 'ITEMIZED'}, after={'split_policy': 'EQUAL'},
        )
        if order.group.collector_id != order.group.owner_id:
            PaymentQRSettings.objects.using(alias).filter(order_id=order.pk).delete()
            DiningGroup.objects.using(alias).filter(pk=order.group_id).update(collector_id=order.group.owner_id)
            PaymentAudit.objects.using(alias).create(
                order_id=order.pk, actor_id=order.group.owner_id, action='HOST_COLLECTION_ENABLED',
                detail='Equal bill sharing enabled. The host collects member payments; previous collector QR settings cleared.',
            )


class Migration(migrations.Migration):
    dependencies = [('orders', '0001_initial'), ('payments', '0001_initial')]

    operations = [
        migrations.AddField(
            model_name='order', name='split_policy',
            field=models.CharField(max_length=10, default='ITEMIZED', editable=False,
                                   choices=[('EQUAL', 'Equal across all members'), ('ITEMIZED', 'Original item-based allocation')]),
        ),
        migrations.RunPython(enable_equal_drafts, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='order', name='split_policy',
            field=models.CharField(max_length=10, default='EQUAL', editable=False,
                                   choices=[('EQUAL', 'Equal across all members'), ('ITEMIZED', 'Original item-based allocation')]),
        ),
    ]
