FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt requirements-postgres.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-postgres.txt
COPY . .

RUN mkdir -p /data \
    && chown -R 10001:10001 /app /data
USER 10001:10001

EXPOSE 8000
CMD ["sh", "-c", "python manage.py migrate --noinput && python manage.py collectstatic --noinput && gunicorn vitali_web.wsgi:application --bind 0.0.0.0:8000 --workers ${SMARTORDER_WEB_WORKERS:-2} --access-logfile - --error-logfile -"]
