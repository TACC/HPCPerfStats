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

# determine thread count from conf_parser and set gunicorn workers = thread_count*2+1
THREAD_COUNT=$(/usr/local/bin/python3 -c "from hpcperfstats import conf_parser; print(conf_parser.get_total_cores())")
WORKERS=$((THREAD_COUNT * 2 + 1))

# gunicorn is the django web server
/usr/local/bin/gunicorn hpcperfstats.site.hpcperfstats_site.wsgi --bind 0.0.0.0:8000  \
  --env DJANGO_SETTINGS_MODULE=hpcperfstats.site.hpcperfstats_site.settings -u hpcperfstats \
  --workers=${WORKERS} --timeout 600 --preload --max-requests 100 --access-logfile - --error-logfile - 

