from pathlib import PurePosixPath

from django.http import FileResponse, Http404
from django.shortcuts import render
from django.views.decorators.http import require_safe
from PIL import Image, UnidentifiedImageError


@require_safe
def public_menu_image(request, path):
    """Serve only referenced public dish images; private QR files have no route."""
    from menu.models import MenuItem

    parts = PurePosixPath(path).parts
    if not parts or parts[0] != 'menu' or '..' in parts or '\\' in path:
        raise Http404
    item = MenuItem.objects.filter(image=path).only('image').first()
    if item is None:
        raise Http404
    try:
        with item.image.storage.open(item.image.name, 'rb') as source:
            with Image.open(source) as decoded:
                content_type = {
                    'PNG': 'image/png', 'JPEG': 'image/jpeg',
                    'WEBP': 'image/webp', 'GIF': 'image/gif',
                }.get(decoded.format)
                if content_type is None:
                    raise Http404
                decoded.verify()
        response = FileResponse(item.image.storage.open(item.image.name, 'rb'), content_type=content_type)
    except (FileNotFoundError, OSError, ValueError, SyntaxError, UnidentifiedImageError, Image.DecompressionBombError):
        raise Http404
    response['X-Content-Type-Options'] = 'nosniff'
    return response

def permission_denied(request, exception=None):
    return render(request, 'errors/403.html', status=403)

def page_not_found(request, exception=None):
    return render(request, 'errors/404.html', status=404)

def server_error(request):
    return render(request, 'errors/500.html', status=500)
