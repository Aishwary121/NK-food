from django import forms
from groups.models import Membership
from .models import OrderItem


class SplitForm(forms.Form):
    quantity = forms.IntegerField(min_value=1, max_value=999)
    mode = forms.ChoiceField(choices=OrderItem.SplitMode.choices, label='How should this item be shared?')
    members = forms.ModelMultipleChoiceField(queryset=Membership.objects.none(), widget=forms.CheckboxSelectMultiple,
                                             label='Who is sharing?')

    def __init__(self, *args, item, **kwargs):
        super().__init__(*args, **kwargs)
        members = item.order.group.memberships.select_related('user', 'pair')
        self.fields['members'].queryset = members
        self.fields['quantity'].initial = item.quantity
        self.fields['mode'].initial = item.split_mode
        allocations = {row.member_id: row for row in item.allocations.all()}
        self.fields['members'].initial = list(allocations)
        for member in members:
            allocation = allocations.get(member.pk)
            initial = None
            if allocation:
                if item.split_mode == 'QUANTITY':
                    initial = allocation.quantity
                elif item.split_mode == 'CUSTOM':
                    initial = allocation.amount
            self.fields[f'value_{member.pk}'] = forms.DecimalField(
                label=f'{member.display_name}: quantity / amount / percentage', required=False,
                min_value=0, max_digits=12, decimal_places=4, initial=initial,
            )

    def clean(self):
        data = super().clean()
        selected = data.get('members')
        if selected is None or not data.get('mode'):
            return data
        values = {}
        for member in selected:
            if data['mode'] in ('EQUAL', 'INDIVIDUAL'):
                values[member.pk] = 1
            else:
                value = data.get(f'value_{member.pk}')
                if value is None:
                    self.add_error(f'value_{member.pk}', 'Enter a value for this selected member.')
                else:
                    values[member.pk] = value
        data['values'] = values
        return data


class AdjustmentForm(forms.Form):
    tax = forms.DecimalField(min_value=0, max_digits=10, decimal_places=2, label='Tax amount (INR)')
    discount = forms.DecimalField(min_value=0, max_digits=10, decimal_places=2, label='Discount amount (INR)')
