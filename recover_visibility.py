"""Emergency, explicitly authorized restore of singles visibility."""
import os
from account_visibility import _session, get_account, set_singles_live

if os.getenv('RESTORE_CONFIRM') != 'RESTORE_SINGLES_LIVE':
    raise RuntimeError('Missing restore confirmation')
with _session() as session:
    before = get_account(session)
    print(f'INFO     singles_live before recovery: {before["singles_live"]}', flush=True)
    if not before['singles_live']:
        after = set_singles_live(session, True)
        if after['singles_live'] is not True:
            raise RuntimeError('Restore not verified')
    print('SUCCESS  singles_live=True verified', flush=True)
