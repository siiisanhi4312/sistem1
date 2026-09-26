from django.contrib import admin
from django.http import HttpResponse
from django.urls import include, path


def home(request):
    return HttpResponse(
        'SEASTEAM API is running. Use /api/ endpoints.',
        content_type='text/plain',
    )


def favicon(request):
    return HttpResponse('', content_type='image/x-icon')


urlpatterns = [
    path('', home, name='home'),
    path('favicon.ico', favicon, name='favicon'),
    path('admin/', admin.site.urls),
    path('api/', include('prediction.urls')),
]
