from django.urls import path
from . import views

app_name = 'groups'
urlpatterns = [
    path('', views.group_list, name='list'),
    path('create/', views.create, name='create'),
    path('join/', views.join, name='join'),
    path('<int:pk>/', views.detail, name='detail'),
    path('<int:pk>/pair/', views.pair, name='pair'),
    path('<int:pk>/collector/', views.collector, name='collector'),
    path('<int:pk>/leave/', views.leave, name='leave'),
    path('<int:pk>/cancel/', views.cancel, name='cancel'),
    path('<int:pk>/complete/', views.complete, name='complete'),
    path('<int:pk>/join-qr.png', views.join_qr, name='join_qr'),
]
