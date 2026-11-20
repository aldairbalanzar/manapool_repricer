"""Scheduled ManaPool repricing coordinator. Read-only until write API is verified.

The script fails closed if live updates are requested: it does not guess at
ManaPool write endpoints or alter inventory quantities.
"""
import csv
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPORT = Path('repricing_report.csv')
ACTIONS = {'floor_increase', 'competitive_decrease'}


def log(level, message):
    print(f'{level:<8} {message}', flush=True)


def main():
    mode = os.getenv('REPRICING_MODE', 'preview').strip().lower()
    if mode not in {'preview', 'live'}:
        raise ValueError('REPRICING_MODE must be preview or live')
    if mode == 'live':
        raise RuntimeError(
            'Live writes are disabled: ManaPool price-update endpoint and '
            'request schema have not been verified. No API writes were attempted.'
        )

    if os.getenv('SCHEDULED_RUN') == '1':
        now = datetime.now(ZoneInfo('America/New_York'))
        if now.hour != 20:
            log('INFO', f'Skipping: New York local time is {now:%Y-%m-%d %H:%M %Z}; expected 20:00')
            return

    log('INFO', 'Starting scheduled repricing preview (read-only)')
    subprocess.run([sys.executable, 'repricing_preview.py'], check=True)
    if not REPORT.is_file():
        raise RuntimeError('Expected repricing_report.csv was not created')

    counts = Counter()
    with REPORT.open(newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        required = {'product_id', 'quantity', 'current_price', 'target_price', 'action'}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError('Report is missing required columns')
        seen = set()
        for row in reader:
            pid = row['product_id']
            if not pid or pid in seen:
                raise ValueError('Report contains missing or duplicate product ID')
            seen.add(pid)
            if int(row['quantity']) <= 0:
                raise ValueError(f'Non-active listing in report: {pid}')
            action = row['action']
            counts[action] += 1
            if action in ACTIONS:
                from decimal import Decimal
                target = Decimal(row['target_price'])
                current = Decimal(row['current_price'])
                if target < Decimal('0.75') or target == current:
                    raise ValueError(f'Unsafe target for {pid}')
                if action == 'floor_increase' and not (target == Decimal('0.75') and current < Decimal('0.75')):
                    raise ValueError(f'Invalid floor increase for {pid}')
                if action == 'competitive_decrease':
                    if not target < current:
                        raise ValueError(f'Invalid decrease for {pid}')
                    market = Decimal(row['market_low'])
                    expected = max(Decimal('0.75'), market + Decimal('0.01'))
                    if target != expected or current <= market:
                        raise ValueError(f'Invalid one-cent-above-low target for {pid}')
            elif action not in {'unchanged', 'review'}:
                raise ValueError(f'Unexpected action {action!r} for {pid}')
    log('SUCCESS', f'Validated {len(seen)} active report rows')
    for action, count in sorted(counts.items()):
        log('INFO', f'{action}: {count}')
    log('WARNING', 'Read-only mode: no ManaPool prices or quantities were changed')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        log('ERROR', f'{type(exc).__name__}: {exc}')
        sys.exit(1)
