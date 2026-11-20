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
BATCH_SIZE = 500
PAUSE_SECONDS = 1.0
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


def make_record(row, old, target, qty, status, details=''):
    return dict(timestamp_utc=datetime.now(timezone.utc).isoformat(),
                product_id=row['product_id'], card_name=row['card_name'],
                action=row['action'], old_price_cents=old,
                target_price_cents=target, quantity_before=qty,
                quantity_after='', status=status, details=details)


def verify_bulk_response(selected, body):
    """Validate every returned item, without assuming a 200 means full success."""
    if not isinstance(body, dict) or not isinstance(body.get('inventory'), list) or not isinstance(body.get('skipped'), list):
        raise ValueError('Missing bulk response arrays')
    expected = {row['product_id']: (target, qty) for row, old, target, qty in selected}
    returned = {}
    for item in body['inventory']:
        if not isinstance(item, dict):
            raise ValueError('Malformed inventory entry')
        pid = item.get('product_id')
        if pid not in expected or pid in returned:
            raise ValueError('Unexpected or duplicate inventory product ID')
        target, qty = expected[pid]
        raw_price, raw_qty = item.get('price_cents'), item.get('quantity')
        valid = (item.get('product_type') == 'mtg_single' and
                 type(raw_price) in (int, float) and raw_price == target and
                 type(raw_qty) in (int, float) and raw_qty == qty)
        returned[pid] = ('VERIFIED_RESPONSE' if valid else 'VERIFY_FAILED',
                         '' if valid else 'Returned price, type, or quantity mismatch', raw_qty)
    skipped = {}
    for item in body['skipped']:
        if not isinstance(item, dict):
            raise ValueError('Malformed skipped entry')
        pid = item.get('product_id')
        if pid not in expected or pid in returned or pid in skipped:
            raise ValueError('Unexpected or duplicate skipped product ID')
        skipped[pid] = ('API_SKIPPED', str(item.get('reason', 'Unknown reason')), '')
    return {pid: returned.get(pid) or skipped.get(pid) or
            ('MISSING_RESPONSE', 'Product absent from response', '') for pid in expected}



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
    stale = 0
    batches = (len(candidates) + BATCH_SIZE - 1) // BATCH_SIZE
    with JOURNAL.open('w', newline='', encoding='utf-8') as file:
        writer = csv.DictWriter(file, fieldnames=FIELDS)
        writer.writeheader()
        file.flush()
        for batch_no, offset in enumerate(range(0, len(candidates), BATCH_SIZE), 1):
            if time.monotonic() - start > DEADLINE_SECONDS:
                raise RuntimeError('Internal runtime deadline reached; restoring visibility')
            # Re-fetch before each batch to avoid writing stale prices or quantities.
            fresh = fetch_inventory()
            selected = []
            for row, old, target, qty in candidates[offset:offset+BATCH_SIZE]:
                if not check_fresh(row, old, qty, fresh.get(row['product_id'])):
                    writer.writerow(make_record(row, old, target, qty, 'SKIPPED_STALE',
                                                'Price, quantity, or product type changed'))
                    stale += 1
                else:
                    selected.append((row, old, target, qty))
            file.flush()
            if not selected:
                continue
            payload = [
                {'product_type': 'mtg_single', 'product_id': row['product_id'],
                 'price_cents': target, 'quantity': None}
                for row, old, target, qty in selected
            ]
            log('INFO', f'Bulk batch {batch_no}/{batches}: writing {len(payload)} prices')
            try:
                response = session.post(f'{BASE}/seller/inventory/product',
                                        json=payload, timeout=90)
            except requests.RequestException as exc:
                for row, old, target, qty in selected:
                    writer.writerow(make_record(row, old, target, qty, 'UNKNOWN_NETWORK',
                                                type(exc).__name__))
                file.flush()
                raise RuntimeError('Bulk write outcome unknown; STOP and inspect inventory') from exc
            if response.status_code != 200:
                for row, old, target, qty in selected:
                    writer.writerow(make_record(row, old, target, qty, 'HTTP_ERROR',
                                                f'HTTP {response.status_code}'))
                file.flush()
                raise RuntimeError(f'Bulk batch {batch_no} failed: HTTP {response.status_code}')
            try:
                body = response.json()
                outcomes = verify_bulk_response(selected, body)
            except (ValueError, TypeError, KeyError) as exc:
                for row, old, target, qty in selected:
                    writer.writerow(make_record(row, old, target, qty, 'VERIFY_FAILED',
                                                'Invalid bulk response'))
                file.flush()
                raise RuntimeError('Bulk response invalid; inspect inventory before retrying') from exc
            failed = []
            for row, old, target, qty in selected:
                status, details, returned_qty = outcomes[row['product_id']]
                record = make_record(row, old, target, qty, status, details)
                record['quantity_after'] = returned_qty
                writer.writerow(record)
                if status == 'VERIFIED_RESPONSE':
                    verified += 1
                else:
                    failed.append(row['product_id'])
            file.flush()
            if failed:
                raise RuntimeError(f'Bulk batch {batch_no}: {len(failed)} unverified/skipped '
                                   'items; stopping. Review journal before retrying.')
            log('SUCCESS', f'Batch {batch_no}/{batches} verified: {len(selected)}; '
                           f'total verified={verified}, stale skipped={stale}')
            time.sleep(PAUSE_SECONDS)
    log('SUCCESS', f'Completed: {verified} verified updates, {stale} skipped stale')


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
