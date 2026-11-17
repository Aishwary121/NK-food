from io import BytesIO
import qrcode
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from .forms import CollectorForm, GroupForm, JoinForm, PairForm
from .models import DiningGroup
from .services import cancel_group, complete_group, create_group, form_pair, join_group, leave_group, set_collector


def accessible_group(user, pk):
    groups = DiningGroup.objects.all()
    if not user.is_staff:
        groups = groups.filter(memberships__user=user).distinct()
    return get_object_or_404(groups, pk=pk)


@login_required
def group_list(request):
    groups = DiningGroup.objects.filter(memberships__user=request.user).select_related('owner', 'order')
    return render(request, 'groups/list.html', {'groups': groups})


@login_required
def create(request):
    form = GroupForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        group = create_group(owner=request.user, **form.cleaned_data)
        messages.success(request, 'Your table is ready. Share the group code with your friends.')
        return redirect('groups:detail', pk=group.pk)
    return render(request, 'groups/form.html', {'form': form, 'heading': 'Start a group order', 'button': 'Create group'})


@login_required
def join(request):
    form = JoinForm(request.POST or None, initial={'code': request.GET.get('code', '')})
    if request.method == 'POST' and form.is_valid():
        try:
            member = join_group(user=request.user, code=form.cleaned_data['code'])
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, 'You have joined the group.')
            return redirect('groups:detail', pk=member.group_id)
    return render(request, 'groups/form.html', {'form': form, 'heading': 'Join your friends', 'button': 'Join group'})


@login_required
def detail(request, pk):
    group = accessible_group(request.user, pk)
    members = group.memberships.select_related('user', 'pair')
    pair_form = PairForm(group=group)
    collector_form = CollectorForm(group=group)
    return render(request, 'groups/detail.html', {'group': group, 'members': members, 'pair_form': pair_form, 'collector_form': collector_form})


@login_required
@require_POST
def pair(request, pk):
    group = accessible_group(request.user, pk)
    form = PairForm(request.POST, group=group)
    if form.is_valid():
        try:
            form_pair(group=group, actor=request.user, member_ids=[m.pk for m in form.cleaned_data['members']], name=form.cleaned_data['name'])
            messages.success(request, 'Pair saved. Existing food allocations stay unchanged; adjust them in the order if needed.')
        except ValidationError as error:
            messages.error(request, '; '.join(error.messages))
    else:
        messages.error(request, 'Choose two group members and enter a pair name.')
    return redirect('groups:detail', pk=pk)


@login_required
@require_POST
def collector(request, pk):
    group = accessible_group(request.user, pk)
    form = CollectorForm(request.POST, group=group)
    if form.is_valid():
        try:
            set_collector(group=group, actor=request.user, membership=form.cleaned_data['collector'])
            messages.success(request, 'Payment collector updated.')
        except ValidationError as error:
            messages.error(request, '; '.join(error.messages))
    else:
        messages.error(request, 'Select a member of this group.')
    return redirect('groups:detail', pk=pk)


@login_required
@require_POST
def leave(request, pk):
    group = accessible_group(request.user, pk)
    try:
        leave_group(group=group, user=request.user)
    except ValidationError as error:
        messages.error(request, '; '.join(error.messages))
        return redirect('groups:detail', pk=pk)
    return redirect('groups:list')


@login_required
def join_qr(request, pk):
    group = accessible_group(request.user, pk)
    uri = request.build_absolute_uri(reverse('groups:join')) + '?code=' + group.code
    buffer = BytesIO()
    qrcode.make(uri).save(buffer, format='PNG')
    response = HttpResponse(buffer.getvalue(), content_type='image/png')
    response['Cache-Control'] = 'private, no-store'
    return response


@login_required
@require_POST
def cancel(request, pk):
    group = accessible_group(request.user, pk)
    try:
        cancel_group(group=group, actor=request.user)
    except ValidationError as error:
        messages.error(request, '; '.join(error.messages))
    else:
        messages.success(request, 'Group cancelled. Its order history is preserved.')
    return redirect('groups:detail', pk=pk)


@login_required
@require_POST
def complete(request, pk):
    group = accessible_group(request.user, pk)
    try:
        complete_group(group=group, actor=request.user)
    except ValidationError as error:
        messages.error(request, '; '.join(error.messages))
    else:
        messages.success(request, 'The group is complete. Its bill and payment history remain available.')
    return redirect('groups:detail', pk=pk)
