from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path


def healthcheck(request):
    return JsonResponse({
        'status': 'ok',
        'service': 'SEASTEAM backend',
        'api_root': '/api/',
        'documentation': 'Use /api/advisories/shellfish/ or /api/predict/',
    }, status=200)


urlpatterns = [
    path('', healthcheck, name='healthcheck'),
    path('healthz/', healthcheck, name='healthz'),
    path('admin/', admin.site.urls),
    path('api/', include('prediction.urls')),
]
