# Headers for nginx-served edge resources (e.g. /robots.txt).
# Keep Cache-Control aligned with how quickly crawl hints should propagate.
add_header Cache-Control "no-store, no-cache" always;
add_header X-Content-Type-Options "nosniff" always;
