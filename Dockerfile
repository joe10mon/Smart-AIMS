FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first so this layer is cached until requirements change.
COPY requirements.txt requirements-prod.txt ./
RUN pip install --no-cache-dir -r requirements-prod.txt

COPY . .

# Collect static files at build time. The key is a throwaway, used only for this step
# (the real one is supplied at run time and never baked into the image).
RUN DJANGO_SECRET_KEY=build-only-not-a-real-secret python manage.py collectstatic --noinput

# Strip any Windows line endings from the entrypoint, then run as a non-root user.
# /data holds the SQLite database and is mounted as a volume.
RUN sed -i 's/\r$//' docker/entrypoint.sh \
    && chmod +x docker/entrypoint.sh \
    && useradd --create-home --uid 1000 app \
    && mkdir /data \
    && chown app:app /data
USER app

EXPOSE 8000
ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["gunicorn", "SAIMSadmin.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--access-logfile", "-"]
