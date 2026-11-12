from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path
from .views import public_menu_image

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('accounts.urls')),
    path('login/', auth_views.LoginView.as_view(template_name='registration/login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('password/change/', auth_views.PasswordChangeView.as_view(template_name='registration/password_change_form.html', success_url='/accounts/profile/'), name='password_change'),
    path('groups/', include('groups.urls')),
    path('orders/', include('orders.urls')),
    path('payments/', include('payments.urls')),
    path('dashboard/', include('dashboard.urls')),
    path('media/<path:path>', public_menu_image, name='public_menu_image'),
    path('', include('menu.urls')),
]
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

admin.site.site_header = 'N.K. Food Court administration'
admin.site.site_title = 'N.K. Food Court'
admin.site.index_title = 'Restaurant management'

handler403 = 'nk_foodcourt.views.permission_denied'
handler404 = 'nk_foodcourt.views.page_not_found'
handler500 = 'nk_foodcourt.views.server_error'
