#!/bin/sh
# Runs every time the container starts: bring the database up to date, then
# hand over to the real command (gunicorn, by default).
set -e
python manage.py migrate --noinput
exec "$@"
