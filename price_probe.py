"""Read-only inspection of ManaPool's public singles price feed."""
import json
import sys
import requests

URL = 'https://manapool.com/api/v1/prices/singles'


def describe(value, prefix='response', depth=0):
    if isinstance(value, dict):
        print(f'INFO     {prefix}: object with keys={list(value)[:35]}')
        if depth < 2:
            for key, child in list(value.items())[:8]:
                describe(child, f'{prefix}.{key}', depth + 1)
    elif isinstance(value, list):
        print(f'INFO     {prefix}: array length={len(value)}')
        if value and depth < 2:
            describe(value[0], f'{prefix}[0]', depth + 1)
    else:
        print(f'INFO     {prefix}: type={type(value).__name__}')


def main():
    print('INFO     Checking public singles price feed (GET only)', flush=True)
    try:
        response = requests.get(URL, headers={'Accept': 'application/json'}, timeout=90)
    except requests.RequestException as exc:
        print(f'ERROR    Request failed: {type(exc).__name__}')
        return 1
    print(f'INFO     HTTP status: {response.status_code}')
    print(f'INFO     Content-Type: {response.headers.get("Content-Type", "unknown")}')
    if response.status_code != 200:
        print('WARNING  Feed unavailable or requires different access; response body not printed')
        return 1
    try:
        payload = response.json()
    except ValueError:
        print('ERROR    Response was not JSON')
        return 1
    describe(payload)
    print('SUCCESS  Read-only price-feed inspection completed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
