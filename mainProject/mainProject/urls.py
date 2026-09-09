from django.contrib import admin
from django.urls import include, path
from prediction.views import health_check

urlpatterns = [
    path('', health_check, name='health_check'),
    path('admin/', admin.site.urls),
    path('api/', include('prediction.urls')),
]
