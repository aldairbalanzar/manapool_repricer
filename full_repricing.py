"""ManaPool price-only production repricer, with bounded execution and CSV audit.

No quantity writes. Always use account_visibility.run_offline to restore original
singles visibility on normal exit/errors. GitHub runner termination is NOT a
reliable finally handler; a separate manual recovery workflow is provided.
"""
import csv
import os
import signal
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import requests
from account_visibility import run_offline
from inventory import fetch_inventory, quantity
from repricing_preview import MIN_PRICE_CENTS, fetch_market_prices, generate_report, parse_cents

BASE = 'https://manapool.com/api/v1'
REPORT = Path('repricing_report.csv')
JOURNAL = Path('repricing_updates.csv')
FIELDS = ['timestamp_utc','product_id','card_name','action','old_price_cents','target_price_cents',
          'quantity_before','quantity_after','status','details']
MAX_UPDATES = 10000
REFRESH_EVERY = 100
PAUSE_SECONDS = 0.35
DEADLINE_SECONDS = 125 * 60  # Must finish before Actions 150-minute timeout.


def log(level, message):
    print(f'{level:<8} {message}', flush=True)


def cents(value):
    try:
        d = Decimal(str(value)) * 100
        return int(d) if d.is_finite() and d == d.to_integral_value() else None
    except (ValueError, ArithmeticError, TypeError):
        return None


def candidates_from_report():
    result = []
    seen = set()
    with REPORT.open(newline='', encoding='utf-8') as file:
        reader = csv.DictReader(file)
        required = {'product_id','card_name','action','quantity','current_price','target_price',
                    'market_low','verification_status'}
        if not required.issubset(reader.fieldnames or []):
            raise RuntimeError('Incomplete pricing report')
        for row in reader:
            pid = row['product_id']
            UUID(pid)
            if pid in seen:
                raise RuntimeError(f'Duplicate product ID: {pid}')
            seen.add(pid)
            if row['action'] not in {'floor_increase','competitive_decrease'}:
                continue
            old, target = cents(row['current_price']), cents(row['target_price'])
            market = cents(row['market_low']) if row['market_low'] else None
            qty = int(row['quantity'])
            if old is None or target is None or qty <= 0 or target < MIN_PRICE_CENTS or target == old:
                raise RuntimeError(f'Unsafe candidate: {pid}')
            if row['action'] == 'floor_increase':
                valid = old < MIN_PRICE_CENTS and target == MIN_PRICE_CENTS and row['verification_status'] in {
                    'floor_increase', 'unmatched_product_floor_increase'}
            else:
                valid = (market is not None and market > 0 and old > market and
                         target < old and target == max(MIN_PRICE_CENTS, market + 1) and
                         row['verification_status'] == 'one_cent_above_market_low')
            if not valid:
                raise RuntimeError(f'Invalid price proposal: {pid}')
            result.append((row, old, target, qty))
    if len(result) > MAX_UPDATES:
        raise RuntimeError(f'Eligible updates {len(result)} exceed safety cap {MAX_UPDATES}')
    return result


def check_fresh(row, old, qty, current):
    return (current is not None and quantity(current) == qty and qty > 0 and
            parse_cents(current.get('price_cents')) == old and
            str(current.get('product_type') or 'mtg_single') == 'mtg_single')


def run_updates():
    start = time.monotonic()
    if REPORT.exists():
        REPORT.unlink()
    log('INFO', 'Generating fresh catalog and inventory report while singles are offline')
    inventory = fetch_inventory()
    active = {pid: item for pid, item in inventory.items() if quantity(item) > 0}
    if not active:
        raise RuntimeError('No active inventory; stopping')
    generate_report(active, fetch_market_prices(active))
    candidates = candidates_from_report()
    log('INFO', f'Eligible live price changes: {len(candidates)}; safety cap {MAX_UPDATES}')
    fresh = fetch_inventory()
    # Fail before ANY write if a selected candidate changed during report generation.
    for row, old, target, qty in candidates:
        if not check_fresh(row, old, qty, fresh.get(row['product_id'])):
            raise RuntimeError(f'Preflight inventory changed: {row["product_id"]}; no writes attempted')
    session = requests.Session()
    session.headers.update({'X-ManaPool-Email': os.environ['MANAPOOL_EMAIL'],
                            'X-ManaPool-Access-Token': os.environ['MANAPOOL_TOKEN'],
                            'Accept':'application/json','Content-Type':'application/json'})
    verified = 0
    skipped = 0
    with JOURNAL.open('w', newline='', encoding='utf-8') as file:
        writer = csv.DictWriter(file, fieldnames=FIELDS)
        writer.writeheader()
        file.flush()
        for i, (row, old, target, qty) in enumerate(candidates):
            if time.monotonic() - start > DEADLINE_SECONDS:
                raise RuntimeError('Internal runtime deadline reached; stopping and restoring visibility')
            pid = row['product_id']
            if i and i % REFRESH_EVERY == 0:
                log('INFO', f'Refreshing inventory before update {i+1}/{len(candidates)}')
                fresh = fetch_inventory()
            stamp = datetime.now(timezone.utc).isoformat()
            record = dict(timestamp_utc=stamp, product_id=pid, card_name=row['card_name'],
                          action=row['action'], old_price_cents=old, target_price_cents=target,
                          quantity_before=qty, quantity_after='', status='', details='')
            if not check_fresh(row, old, qty, fresh.get(pid)):
                record.update(status='SKIPPED_STALE', details='Price, quantity, or product type changed')
                writer.writerow(record); file.flush()
                skipped += 1
                continue
            log('INFO', f'Update {i+1}/{len(candidates)}: {pid} {old}c -> {target}c')
            try:
                response = session.put(f'{BASE}/seller/inventory/product/mtg_single/{pid}',
                                       json={'price_cents': target, 'quantity': None}, timeout=25)
            except requests.RequestException as exc:
                record.update(status='UNKNOWN_NETWORK', details=type(exc).__name__)
                writer.writerow(record); file.flush()
                raise RuntimeError('Network failure; write outcome unknown. STOP and inspect journal') from exc
            if response.status_code != 200:
                record.update(status='HTTP_ERROR', details=f'HTTP {response.status_code}')
                writer.writerow(record); file.flush()
                raise RuntimeError(f'Price write failed: HTTP {response.status_code} for {pid}')
            try:
                item = response.json()['inventory']
                returned_qty = item['quantity']
                valid = (item['product_id'] == pid and parse_cents(item['price_cents']) == target
                         and type(returned_qty) is int and returned_qty == qty)
            except (ValueError, TypeError, KeyError):
                valid, returned_qty = False, ''
            record.update(status='VERIFIED_RESPONSE' if valid else 'VERIFY_FAILED', quantity_after=returned_qty)
            writer.writerow(record); file.flush()
            if not valid:
                raise RuntimeError(f'Unexpected update response for {pid}; stopping')
            verified += 1
            if verified % 100 == 0:
                log('SUCCESS', f'Progress: {verified} verified, {skipped} skipped')
            time.sleep(PAUSE_SECONDS)
    log('SUCCESS', f'Completed: {verified} verified updates, {skipped} skipped stale')


def main():
    if os.getenv('FULL_REPRICING_CONFIRM') != 'CONFIRM_FULL_REPRICING':
        raise RuntimeError('Full live repricing not authorized; no changes made')
    if not os.getenv('MANAPOOL_EMAIL') or not os.getenv('MANAPOOL_TOKEN'):
        raise RuntimeError('ManaPool credentials are missing')
    if os.getenv('SCHEDULED_RUN') == '1':
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo('America/New_York'))
        if now.hour != 20:
            log('INFO', f'Skipping UTC schedule duplicate at {now:%Y-%m-%d %H:%M %Z}')
            return
    # SIGTERM is delivered on some cancellations; convert to exception to invoke finally.
    def interrupted(signum, frame):
        raise RuntimeError('Received termination signal; attempting visibility restoration')
    signal.signal(signal.SIGTERM, interrupted)
    run_offline(run_updates, log)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        message = str(exc)
        for secret in (os.getenv('MANAPOOL_EMAIL'), os.getenv('MANAPOOL_TOKEN')):
            if secret:
                message = message.replace(secret, '[REDACTED]')
        log('ERROR', f'{type(exc).__name__}: {message}')
        sys.exit(1)
