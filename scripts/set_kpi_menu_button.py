import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv


def main():
    load_dotenv()
    if len(sys.argv) != 2 or not sys.argv[1].startswith('https://'):
        raise SystemExit(
            'Usage: python scripts/set_kpi_menu_button.py https://example.trycloudflare.com'
        )
    token = os.getenv('TELEGRAM_API_KEY')
    if not token:
        raise SystemExit('TELEGRAM_API_KEY is not configured')
    url = sys.argv[1].rstrip('/')
    response = requests.post(
        f'https://api.telegram.org/bot{token}/setChatMenuButton',
        json={
            'menu_button': {
                'type': 'web_app',
                'text': 'OMG VR',
                'web_app': {'url': url},
            },
        },
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get('ok'):
        raise SystemExit(payload.get('description', 'Telegram rejected menu button'))
    runtime_url_path = Path('data/kpi_webapp_url.txt')
    runtime_url_path.parent.mkdir(parents=True, exist_ok=True)
    runtime_url_path.write_text(url, encoding='utf-8')
    print(f'OMG VR menu button updated: {url}')


if __name__ == '__main__':
    main()
