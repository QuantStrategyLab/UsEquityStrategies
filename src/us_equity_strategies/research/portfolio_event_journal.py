"""Local, create-only intraday event journal for artificial research inputs.

This namespace is distinct from QPK's daily strategy-state chain. Records prove
content continuity, not source authority, financial correctness or power-loss
durability. No file is overwritten, deleted or automatically repaired.
"""
from copy import deepcopy
from datetime import date
import json
import math
from pathlib import Path
import re

from quant_platform_kit.cloud.local_provider import LocalObjectStore
from quant_platform_kit.common.strategy_risk_state import StrategyRiskStateIdentity

from .auto_portfolio_account import digest, utc


SCHEMA_VERSION = 'qsl.portfolio-event-journal.v1'
NAMESPACE = 'portfolio_event_journal_v1'
_FIELDS = {'schema_version', 'identity', 'kind', 'event_id', 'sequence', 'observed_at',
    'effective_session', 'input_sha256', 'state', 'state_sha256',
    'previous_record_sha256', 'provenance', 'record_sha256'}
_SHA256 = re.compile(r'^[0-9a-f]{64}$')
_EVENT_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$')


def _require(condition, code):
    if not condition: raise ValueError(code)


def _json(value):
    """Reject non-JSON Python coercions and non-finite nested values."""
    if type(value) is dict:
        _require(all(type(key) is str for key in value), 'JOURNAL_JSON_KEYS_INVALID')
        for item in value.values(): _json(item)
    elif type(value) is list:
        for item in value: _json(item)
    else:
        _require(value is None or type(value) in (str, bool, int)
            or (type(value) is float and math.isfinite(value)), 'JOURNAL_FINITE_JSON_REQUIRED')


def _identity(value):
    _require(type(value) is dict and all(type(item) is str for item in value.values()),
        'JOURNAL_IDENTITY_INVALID')
    try: normalized = StrategyRiskStateIdentity.from_dict(value).to_dict()
    except (ValueError, TypeError, KeyError) as exc:
        raise ValueError('JOURNAL_IDENTITY_INVALID') from exc
    _require(normalized == value, 'JOURNAL_IDENTITY_NOT_CANONICAL')
    return normalized


def _sha(value):
    return type(value) is str and _SHA256.fullmatch(value) is not None


def _scope(identity):
    return {key: identity[key] for key in ('strategy_profile', 'account_scope')}


def _record(value):
    _require(type(value) is dict and set(value) == _FIELDS, 'JOURNAL_RECORD_FIELDS_INVALID')
    _json(value)
    _identity(value['identity'])
    _require(value['schema_version'] == SCHEMA_VERSION, 'JOURNAL_SCHEMA_UNSUPPORTED')
    _require(value['kind'] in ('INITIALIZE', 'EVENT', 'MIGRATE_IN', 'MIGRATE_OUT'), 'JOURNAL_KIND_INVALID')
    _require(type(value['event_id']) is str and _EVENT_ID.fullmatch(value['event_id']) is not None,
        'JOURNAL_EVENT_ID_INVALID')
    _require(type(value['sequence']) is int and value['sequence'] >= 0, 'JOURNAL_SEQUENCE_INVALID')
    _require(type(value['effective_session']) is str, 'JOURNAL_SESSION_INVALID')
    try:
        session = date.fromisoformat(value['effective_session']).isoformat()
        observed = utc(value['observed_at'])
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError('JOURNAL_TIME_INVALID') from exc
    _require(session == value['effective_session'] and observed.date().isoformat() == session,
        'JOURNAL_UTC_SESSION_MISMATCH')
    _require(observed.isoformat() == value['observed_at'], 'JOURNAL_TIME_NOT_CANONICAL')
    _require(_sha(value['input_sha256']) and _sha(value['state_sha256'])
        and _sha(value['record_sha256']), 'JOURNAL_HASH_INVALID')
    _require(type(value['state']) is dict and digest(value['state']) == value['state_sha256'],
        'JOURNAL_STATE_HASH_MISMATCH')
    previous = value['previous_record_sha256']
    _require(previous is None or _sha(previous), 'JOURNAL_PREDECESSOR_INVALID')
    root = value['kind'] in ('INITIALIZE', 'MIGRATE_IN')
    _require((root and previous is None and value['sequence'] == 0)
        or (not root and previous is not None and value['sequence'] > 0), 'JOURNAL_KIND_POSITION_INVALID')
    provenance = value['provenance']
    _require((value['kind'] == 'EVENT' and provenance is None)
        or (value['kind'] != 'EVENT' and type(provenance) is dict and bool(provenance)),
        'JOURNAL_PROVENANCE_INVALID')
    expected = digest({key: item for key, item in value.items() if key != 'record_sha256'})
    _require(expected == value['record_sha256'], 'JOURNAL_RECORD_HASH_MISMATCH')


def _linked(previous, current):
    _require(previous['kind'] != 'MIGRATE_OUT', 'JOURNAL_MIGRATED_CHAIN_TERMINAL')
    _require(current['previous_record_sha256'] == previous['record_sha256']
        and current['sequence'] == previous['sequence'] + 1, 'JOURNAL_PREDECESSOR_MISMATCH')
    _require(current['identity'] == previous['identity'], 'JOURNAL_IDENTITY_CHANGED')
    _require(current['effective_session'] >= previous['effective_session']
        and utc(current['observed_at']) >= utc(previous['observed_at']), 'JOURNAL_EVENT_TIME_REGRESSION')
    _require(current['event_id'] != previous['event_id'], 'JOURNAL_EVENT_ID_REUSED')


def validate_event_chain(chain):
    """Validate complete, ordered records; an empty journal has no implied state."""
    _require(type(chain) in (list, tuple), 'JOURNAL_CHAIN_INVALID')
    previous = None
    event_ids = set()
    for item in chain:
        _record(item)
        _require(item['event_id'] not in event_ids, 'JOURNAL_EVENT_ID_REUSED')
        event_ids.add(item['event_id'])
        if previous is None:
            _require(item['previous_record_sha256'] is None, 'JOURNAL_ROOT_MISSING')
        else:
            _linked(previous, item)
        previous = item


def build_event_record(*, identity, kind, event_id, observed_at, effective_session,
        input_sha256, state, previous=None, provenance=None):
    """Build canonical records; provenance semantics belong to the scoped caller."""
    if previous is not None: _record(previous)
    try: observed_at = utc(observed_at).isoformat()
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError('JOURNAL_TIME_INVALID') from exc
    _json(state)
    value = dict(schema_version=SCHEMA_VERSION, identity=deepcopy(identity), kind=kind,
        event_id=event_id, sequence=0 if previous is None else previous['sequence'] + 1,
        observed_at=observed_at, effective_session=effective_session, input_sha256=input_sha256,
        state=deepcopy(state), state_sha256=digest(state),
        previous_record_sha256=previous['record_sha256'] if previous is not None else None,
        provenance=deepcopy(provenance))
    _json(value)
    value['record_sha256'] = digest(value)
    _record(value)
    if previous is not None: _linked(previous, value)
    return value


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, 'JOURNAL_DUPLICATE_JSON_KEY')
        result[key] = value
    return result


class PortfolioEventJournal:
    """One local journal scope backed by QPK's original create-only object store.

    The caller must control the offline directory against external filesystem
    mutation. Scope checks do not claim to eliminate hostile symlink races.
    Constructor and reads create no directories. Interrupted files block the
    journal; recovery requires a separately authorized procedure.
    """
    def __init__(self, local_dir, *, offline_root, object_store=None):
        _require(isinstance(local_dir, (str, Path)) and isinstance(offline_root, (str, Path)),
            'JOURNAL_LOCAL_PATH_REQUIRED')
        _require('://' not in str(local_dir) and '://' not in str(offline_root), 'JOURNAL_LOCAL_PATH_REQUIRED')
        _require('..' not in Path(local_dir).parts, 'JOURNAL_PATH_TRAVERSAL_FORBIDDEN')
        supplied_root = Path(offline_root).absolute()
        self.offline_root = supplied_root.resolve(strict=True)
        _require(self.offline_root.is_dir(), 'JOURNAL_OFFLINE_ROOT_INVALID')
        supplied_local = Path(local_dir).absolute()
        # Canonicalize the explicitly approved root prefix (e.g. macOS /var)
        # while retaining every descendant component for symlink inspection.
        self.local_dir = (self.offline_root / supplied_local.relative_to(supplied_root)
            if supplied_local.is_relative_to(supplied_root) else supplied_local)
        _require(self.local_dir.resolve() != self.offline_root, 'JOURNAL_SCOPE_SUBDIRECTORY_REQUIRED')
        self.object_store = LocalObjectStore() if object_store is None else object_store
        _require(isinstance(self.object_store, LocalObjectStore), 'JOURNAL_LOCAL_OBJECT_STORE_REQUIRED')
        self._safe_path(self.local_dir)
        _require(not self.local_dir.exists() or self.local_dir.is_dir(), 'JOURNAL_DIRECTORY_INVALID')

    def _safe_path(self, path):
        _require(path.resolve().is_relative_to(self.offline_root), 'JOURNAL_PATH_OUTSIDE_OFFLINE_ROOT')
        cursor = path
        while cursor != self.offline_root:
            _require(not cursor.is_symlink(), 'JOURNAL_SYMLINK_FORBIDDEN')
            if cursor != path:
                _require(not cursor.exists() or cursor.is_dir(), 'JOURNAL_PARENT_DIRECTORY_INVALID')
            _require(cursor != cursor.parent, 'JOURNAL_PATH_OUTSIDE_OFFLINE_ROOT')
            cursor = cursor.parent

    def _scope_location(self, identity):
        return str(self.local_dir / NAMESPACE / digest(_scope(_identity(identity))))

    def _location(self, identity):
        return str(Path(self._scope_location(identity)) / 'identities' / digest(_identity(identity)))

    def _root_location(self, identity, kind='INITIALIZE'):
        identity = _identity(identity)
        _require(kind in ('INITIALIZE', 'MIGRATE_IN'), 'JOURNAL_ROOT_KIND_INVALID')
        if kind == 'INITIALIZE':
            return str(Path(self._scope_location(identity)) / 'origin.json')
        return str(Path(self._location(identity)) / 'root.json')

    def _origin(self, identity):
        """The INITIALIZE record itself is the unique create-only scope origin."""
        root = Path(self._root_location(identity))
        self._safe_path(root)
        if root.parent.exists():
            _require(root.parent.is_dir(), 'JOURNAL_ORIGIN_DIRECTORY_INVALID')
            for path in root.parent.iterdir():
                self._safe_path(path)
                _require((path.name == 'origin.json' and path.is_file())
                    or (path.name == 'identities' and path.is_dir()), 'JOURNAL_ORIGIN_LAYOUT_INVALID')
        if not self.object_store.exists(str(root)): return None
        value = self._read(root)
        _require(value['kind'] == 'INITIALIZE' and _scope(value['identity']) == _scope(identity),
            'JOURNAL_ORIGIN_SCOPE_OR_KIND_INVALID')
        return value

    def _require_unused_scope(self, identity):
        """Refuse a fresh origin when its scope still contains identity records."""
        if self._origin(identity) is not None: return
        identities = Path(self._scope_location(identity)) / 'identities'
        self._safe_path(identities)
        if not identities.exists(): return
        _require(identities.is_dir(), 'JOURNAL_IDENTITY_DIRECTORY_INVALID')
        for directory in identities.iterdir():
            self._safe_path(directory)
            _require(directory.is_dir() and _sha(directory.name), 'JOURNAL_IDENTITY_DIRECTORY_INVALID')
            for entry in directory.iterdir():
                self._safe_path(entry)
                _require(entry.name in ('root.json', 'successors'), 'JOURNAL_UNCOMMITTED_OR_UNKNOWN_FILE')
                if entry.name == 'root.json':
                    _require(entry.is_file(), 'JOURNAL_LAYOUT_INVALID')
                    paths = [entry]
                else:
                    _require(entry.is_dir(), 'JOURNAL_LAYOUT_INVALID')
                    paths = list(entry.iterdir())
                for path in paths:
                    self._safe_path(path)
                    _require(path.is_file() and (path.name == 'root.json'
                        or re.fullmatch(r'[0-9a-f]{64}\.json', path.name) is not None),
                        'JOURNAL_UNCOMMITTED_OR_UNKNOWN_FILE')
                    raise ValueError('JOURNAL_SCOPE_HISTORY_WITHOUT_ORIGIN')

    def _inventory(self, identity):
        directory = Path(self._location(identity))
        self._safe_path(directory)
        _require(not directory.exists() or directory.is_dir(), 'JOURNAL_DIRECTORY_INVALID')
        if not directory.exists(): return directory, {}
        for path in directory.iterdir():
            self._safe_path(path)
            _require(path.name in ('root.json', 'successors'), 'JOURNAL_UNCOMMITTED_OR_UNKNOWN_FILE')
            _require(path.is_dir() if path.name == 'successors' else path.is_file(), 'JOURNAL_LAYOUT_INVALID')
        successors = directory / 'successors'
        if successors.exists():
            for path in successors.iterdir():
                self._safe_path(path)
                _require(path.is_file() and re.fullmatch(r'[0-9a-f]{64}\.json', path.name) is not None,
                    'JOURNAL_UNCOMMITTED_OR_UNKNOWN_FILE')
        paths = self.object_store.list(str(successors))
        for value in paths:
            path = Path(value)
            self._safe_path(path)
            _require(path.parent == successors and re.fullmatch(r'[0-9a-f]{64}\.json', path.name) is not None,
                'JOURNAL_OBJECT_LIST_INVALID')
        return directory, {Path(value).stem: Path(value) for value in paths}

    def _read(self, path):
        self._safe_path(path)
        try:
            value = json.loads(self.object_store.read_text(str(path)), object_pairs_hook=_unique_object)
        except (OSError, ValueError, TypeError) as exc:
            raise ValueError('JOURNAL_STORED_RECORD_UNREADABLE') from exc
        _record(value)
        return value

    def load_chain(self, identity):
        """Read and validate every record, refusing orphaned or partial objects."""
        identity = _identity(identity)
        directory, successors = self._inventory(identity)
        origin = self._origin(identity)
        root_path = directory / 'root.json'
        self._safe_path(root_path)
        root = None
        if self.object_store.exists(str(root_path)):
            root = self._read(root_path)
            _require(root['kind'] == 'MIGRATE_IN', 'JOURNAL_IDENTITY_ROOT_KIND_INVALID')
            _require(origin is None or origin['identity'] != identity, 'JOURNAL_DUPLICATE_ROOT_ADDRESS')
        elif origin is not None and origin['identity'] == identity:
            root = origin
        if root is None:
            _require(not successors, 'JOURNAL_ORPHAN_SUCCESSOR')
            return []
        chain = [root]
        _require(chain[0]['identity'] == identity, 'JOURNAL_IDENTITY_MISMATCH')
        visited = set()
        while chain[-1]['record_sha256'] in successors:
            previous = chain[-1]['record_sha256']
            _require(previous not in visited, 'JOURNAL_CHAIN_CYCLE')
            visited.add(previous)
            next_record = self._read(successors[previous])
            _require(next_record['previous_record_sha256'] == previous, 'JOURNAL_ADDRESS_MISMATCH')
            chain.append(next_record)
        _require(visited == set(successors), 'JOURNAL_ORPHAN_SUCCESSOR')
        validate_event_chain(chain)
        return chain

    def append(self, record):
        """CAS on predecessor address; exceptions never trigger a write retry."""
        _record(record)
        record = deepcopy(record)
        identity = record['identity']
        chain = self.load_chain(identity)
        for item in chain:
            if item['event_id'] == record['event_id']:
                _require(item == record, 'JOURNAL_EVENT_ID_CONFLICT')
                return dict(status='already_appended', head_sha256=chain[-1]['record_sha256'], chain_length=len(chain))
        validate_event_chain([*chain, record])
        previous = record['previous_record_sha256']
        if record['kind'] == 'INITIALIZE': self._require_unused_scope(identity)
        path = (Path(self._root_location(identity, record['kind'])) if previous is None
            else Path(self._location(identity)) / f'successors/{previous}.json')
        self._safe_path(path)
        encoded = json.dumps(record, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
        created = self.object_store.create_text(str(path), encoded, content_type='application/json')
        _require(type(created) is bool, 'JOURNAL_CREATE_RECEIPT_INVALID')
        if not created:
            _require(self._read(path) == record, 'JOURNAL_PREDECESSOR_CONFLICT')
        recovered = self.load_chain(identity)
        _require(any(item == record for item in recovered), 'JOURNAL_APPEND_READBACK_UNCONFIRMED')
        return dict(status='created' if created else 'already_appended',
            head_sha256=recovered[-1]['record_sha256'], chain_length=len(recovered))
