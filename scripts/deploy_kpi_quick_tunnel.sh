#!/usr/bin/env bash
set -euo pipefail

docker compose up -d --build bot kpi_web kpi_tunnel

tunnel_url=""
for _attempt in $(seq 1 30); do
    tunnel_url="$(
        docker compose logs --no-color kpi_tunnel 2>/dev/null \
            | grep -Eo 'https://[a-z0-9-]+\.trycloudflare\.com' \
            | tail -n 1 \
            || true
    )"
    if [ -n "$tunnel_url" ]; then
        break
    fi
    sleep 2
done

if [ -z "$tunnel_url" ]; then
    echo "Cloudflare Quick Tunnel URL was not created within 60 seconds." >&2
    docker compose logs --no-color --tail=100 kpi_tunnel >&2
    exit 1
fi

docker compose exec -T bot \
    python scripts/set_kpi_menu_button.py "$tunnel_url"

echo "KPI Mini App is available at: $tunnel_url"
