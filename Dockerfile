FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY mainProject/requirements.txt /app/mainProject/requirements.txt
RUN python -m pip install --upgrade pip \
    && pip install -r /app/mainProject/requirements.txt

COPY mainProject /app/mainProject

CMD ["sh", "-c", "cd /app/mainProject && python manage.py migrate --noinput && python manage.py collectstatic --noinput && python -m gunicorn mainProject.wsgi:application --bind 0.0.0.0:${PORT:-8000} --workers 1"]
