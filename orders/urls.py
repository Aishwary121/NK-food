from django.urls import path
from . import views

app_name = 'orders'
urlpatterns = [
    path('', views.order_list, name='list'),
    path('cart/add/', views.add, name='add'),
    path('cart/item/<int:pk>/', views.cart_update, name='update_cart'),
    path('<int:pk>/', views.detail, name='detail'),
    path('<int:pk>/cart/', views.cart, name='cart'),
    path('<int:pk>/cart/submit/', views.cart_submit, name='submit_cart'),
    path('<int:pk>/adjustments/', views.adjustments, name='adjustments'),
    path('<int:pk>/finalize/', views.finalize, name='finalize'),
    path('<int:pk>/receipt/', views.receipt, name='receipt'),
    path('item/<int:pk>/split/', views.edit_split, name='split'),
    path('item/<int:pk>/remove/', views.item_remove, name='remove'),
]
