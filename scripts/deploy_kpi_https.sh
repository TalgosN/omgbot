#!/usr/bin/env bash
set -euo pipefail

webapp_url="${1:-https://bot.omg-vr.ru}"
if [[ "$webapp_url" != https://* ]]; then
    echo "Web App URL must start with https://" >&2
    exit 1
fi

docker compose up -d --build --remove-orphans bot kpi_web caddy

ready=""
for _attempt in $(seq 1 30); do
    if curl --fail --silent --show-error "$webapp_url/health" >/dev/null 2>&1; then
        ready="true"
        break
    fi
    sleep 3
done

if [ -z "$ready" ]; then
    echo "HTTPS endpoint did not become ready within 90 seconds." >&2
    docker compose logs --no-color --tail=100 caddy kpi_web >&2
    exit 1
fi

docker compose exec -T bot \
    python scripts/set_kpi_menu_button.py "$webapp_url"

echo "KPI Mini App is available at: $webapp_url"
