from django.urls import path
from . import views

app_name = 'payments'
urlpatterns = [
    path('order/<int:pk>/', views.collector, name='collector'),
    path('order/<int:pk>/settings/', views.settings_view, name='settings'),
    path('order/<int:pk>/static-preview/', views.static_preview, name='static_preview'),
    path('member/<int:pk>/', views.member, name='member'),
    path('member/<int:pk>/qr/', views.qr, name='qr'),
    path('review/<int:pk>/', views.review, name='review'),
]
