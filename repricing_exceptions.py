"""Strict printing/foil exceptions; condition is deliberately ignored."""
import csv
from pathlib import Path
from uuid import UUID

EXCEPTIONS_DIR = Path(__file__).resolve().with_name('card_exception')
REQUIRED = {'scryfall_id', 'card_name', 'set_code', 'is_foil'}


def normalized(value):
    return value.strip().casefold() if isinstance(value, str) else ''


def identity(sid, name, code, foil):
    name, code = normalized(name), normalized(code)
    if not name or not code or type(foil) is not bool:
        raise ValueError('Incomplete card identity')
    return str(UUID(str(sid).strip())), name, code, foil


def _load_file(path):
    result = {}
    with path.open(encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f, strict=True)
        fields = reader.fieldnames or []
        if not REQUIRED.issubset(fields) or len(fields) != len(set(fields)):
            raise ValueError('Invalid exception CSV headers')
        for row in reader:
            try:
                if None in row or any(row[k] is None for k in REQUIRED):
                    raise ValueError('Invalid row length')
                foil = normalized(row['is_foil'])
                if foil not in ('true', 'false'):
                    raise ValueError('is_foil must be true or false')
                key = identity(row['scryfall_id'], row['card_name'], row['set_code'], foil == 'true')
                reason = (row.get('reason') or '').strip()
                result[key] = _merge_reasons(result.get(key, ''), reason)
            except (ValueError, TypeError) as exc:
                raise ValueError(f'{path.name} line {reader.line_num}: {exc}') from exc
    return result


def _merge_reasons(existing, new):
    return '; '.join(dict.fromkeys(x for x in (existing + '; ' + new).split('; ') if x))


def load_exceptions(directory=None):
    directory = Path(directory) if directory is not None else EXCEPTIONS_DIR
    if not directory.is_dir():
        raise ValueError(f'Exception directory missing: {directory}')
    files = sorted(p for p in directory.iterdir() if p.is_file() and p.suffix.lower() == '.csv')
    if not files:
        raise ValueError(f'No exception CSV files in {directory}')
    result = {}
    for path in files:
        try:
            for key, reason in _load_file(path).items():
                result[key] = _merge_reasons(result.get(key, ''), reason)
        except (ValueError, csv.Error, OSError) as exc:
            raise ValueError(f'Cannot load exception file {path}: {exc}') from exc
    return result


def listing_identity(listing, card, variant):
    single = (listing.get('product') or {}).get('single') or {}
    # FO and EF are both foil; NF is non-foil. Never infer unknown finishes.
    def key(sid, name, code, finish):
        if finish not in ('NF', 'FO', 'EF'):
            raise ValueError('Unknown finish')
        return identity(sid, name, code, finish != 'NF')
    keys = []
    for sid, name, code, finish in (
        (card.get('scryfall_id'), card.get('name'), card.get('set_code'), variant.get('finish_id')),
        (single.get('scryfall_id'), single.get('name'), single.get('set'), single.get('finish_id')),
    ):
        if sid:
            keys.append(key(sid, name, code, finish))
    if not keys or any(k != keys[0] for k in keys):
        raise ValueError('Missing or conflicting card identity')
    return keys[0]
