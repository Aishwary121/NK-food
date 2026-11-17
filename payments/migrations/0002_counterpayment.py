from django.conf import settings
from django.db import migrations, models
import django.core.validators
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('payments', '0001_initial'),
        ('orders', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='CounterPayment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('amount', models.DecimalField(decimal_places=2, max_digits=12, validators=[django.core.validators.MinValueValidator(0)])),
                ('recorded_at', models.DateTimeField(auto_now_add=True)),
                ('reference', models.CharField(blank=True, max_length=80)),
                ('order', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='counter_payment', to='orders.order')),
                ('recorded_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='counter_payment_declarations', to=settings.AUTH_USER_MODEL)),
            ],
            options={'constraints': [models.CheckConstraint(condition=models.Q(amount__gte=0), name='nonnegative_counter_payment')]},
        ),
    ]
