"""ManaPool seller singles visibility guard for controlled repricing runs."""
import os
import time
import requests

URL = 'https://manapool.com/api/v1/account'


def _session():
    email, token = os.getenv('MANAPOOL_EMAIL'), os.getenv('MANAPOOL_TOKEN')
    if not email or not token:
        raise RuntimeError('ManaPool credentials are missing')
    s = requests.Session()
    s.headers.update({'X-ManaPool-Email': email, 'X-ManaPool-Access-Token': token,
                      'Accept': 'application/json', 'Content-Type': 'application/json'})
    return s


def get_account(session):
    response = session.get(URL, timeout=30)
    response.raise_for_status()
    account = response.json()
    if type(account.get('singles_live')) is not bool or type(account.get('sealed_live')) is not bool:
        raise RuntimeError('Invalid account visibility response')
    return account


def set_singles_live(session, live):
    response = session.put(URL, json={'singles_live': live}, timeout=30)
    response.raise_for_status()
    account = response.json()
    if type(account.get('singles_live')) is not bool or account['singles_live'] != live:
        raise RuntimeError('Account update response did not confirm requested singles visibility')
    for attempt in range(3):
        check = get_account(session)
        if check['singles_live'] == live:
            return check
        time.sleep(2)
    raise RuntimeError('Account visibility could not be verified by GET /account')


def run_offline(operation, log):
    """Restore original state on success/failure; fail closed if unable to pause."""
    session = _session()
    initial = get_account(session)
    originally_live = initial['singles_live']
    log('INFO', f"Initial singles_live={originally_live}; sealed_live={initial['sealed_live']}")
    try:
        if originally_live:
            log('INFO', 'Taking singles listings offline before repricing')
            set_singles_live(session, False)
        else:
            log('INFO', 'Singles already offline; preserving original state')
        if get_account(session)['singles_live'] is not False:
            raise RuntimeError('Singles are not confirmed offline; refusing to update prices')
        return operation()
    finally:
        # Restore only when originally live. If restoration fails, propagate error
        # and clearly alert operator; no subsequent repricing may continue.
        if originally_live:
            log('WARNING', 'Restoring original singles_live=True setting')
            try:
                set_singles_live(session, True)
                log('SUCCESS', 'Original singles visibility restored and verified')
            except Exception:
                log('ERROR', 'CRITICAL: Could not restore singles visibility. Check ManaPool immediately!')
                raise
        else:
            log('SUCCESS', 'Singles remain offline, as they were before this run')
