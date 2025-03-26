#!/bin/sh

echo "Waiting for postgres..."

while ! nc -z db 5432; do
  sleep 0.1
done

echo "PostgreSQL started"


chown -R hpcperfstats:hpcperfstats /hpcperfstats/

# detect if the tables are existing and create if not
/usr/local/bin/python3 hpcperfstats/site/manage.py makemigrations
/usr/local/bin/python3 hpcperfstats/site/manage.py migrate

# gunicorn is the django web server - this is configued for a 40 core machine
/usr/local/bin/gunicorn hpcperfstats.site.hpcperfstats_site.wsgi --bind 0.0.0.0:8000  --env DJANGO_SETTINGS_MODULE=hpcperfstats.site.hpcperfstats_site.settings -u hpcperfstats --workers=81

