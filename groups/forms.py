from django import forms
from .models import DiningGroup, Membership


class GroupForm(forms.ModelForm):
    class Meta:
        model = DiningGroup
        fields = ['name', 'max_members', 'description']
        labels = {'max_members': 'Number of members'}
        widgets = {'description': forms.Textarea(attrs={'rows': 3})}


class JoinForm(forms.Form):
    code = forms.CharField(max_length=8, min_length=8, label='Group code')

    def clean_code(self):
        return self.cleaned_data['code'].strip().upper()


class PairForm(forms.Form):
    name = forms.CharField(max_length=80, initial='Our pair')
    members = forms.ModelMultipleChoiceField(queryset=Membership.objects.none(), widget=forms.CheckboxSelectMultiple)

    def __init__(self, *args, group, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['members'].queryset = group.memberships.select_related('user')

    def clean_members(self):
        members = self.cleaned_data['members']
        if len(members) != 2:
            raise forms.ValidationError('Select exactly two group members.')
        return members


class CollectorForm(forms.Form):
    collector = forms.ModelChoiceField(queryset=Membership.objects.none(), label='Payment collector')

    def __init__(self, *args, group, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['collector'].queryset = group.memberships.select_related('user')
        self.fields['collector'].initial = group.memberships.filter(user=group.collector).first()
