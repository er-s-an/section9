import json
import sqlite3
from contextlib import contextmanager

from s9.store import Rejected, digest, encode, now, uid


class PairCatalog:
    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.tx() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS pairs(id TEXT PRIMARY KEY,data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS idempotency(key TEXT PRIMARY KEY,request_hash TEXT NOT NULL,pair_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);''')

    @contextmanager
    def tx(self):
        with sqlite3.connect(self.path, timeout=15) as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('BEGIN IMMEDIATE')
            yield db

    def get(self, pair_id):
        with self.tx() as db:
            row = db.execute('SELECT data FROM pairs WHERE id=?', (pair_id,)).fetchone()
        if not row:
            raise Rejected('PAIR_NOT_FOUND', '未找到指定 Pair', 404)
        return json.loads(row[0])

    def list(self):
        with self.tx() as db:
            return [json.loads(r[0]) for r in db.execute('SELECT data FROM pairs ORDER BY rowid DESC')]

    def active(self):
        with self.tx() as db:
            row = db.execute("SELECT value FROM meta WHERE key='active_pair'").fetchone()
        return self.get(row[0]) if row else None

    def create(self, spec, request, key=None):
        with self.tx() as db:
            request_hash = digest(request)
            if key:
                old = db.execute('SELECT request_hash,pair_id FROM idempotency WHERE key=?', (key,)).fetchone()
                if old:
                    if old[0] != request_hash:
                        raise Rejected('IDEMPOTENCY_CONFLICT', '幂等键已用于不同 Pair 规格')
                    return json.loads(db.execute('SELECT data FROM pairs WHERE id=?', (old[1],)).fetchone()[0]), False
            pair_id = uid('pair')
            pair = {'pair_id': pair_id, 'status': 'preparing', 'spec_hash': digest(spec), 'immutable_spec': spec,
                    'swarm_run_id': uid('run'), 'baseline_run_id': uid('run'), 'created_at': now(), 'started_at': None,
                    'ended_at': None, 'previous_pair_id': request.get('previous_pair_id'), 'model_calls_started': False,
                    'source_identity': spec['source_identity'], 'comparison_integrity': {'eligible': True, 'reasons': []},
                    'links': {arm: f'/showcase/{arm}?pair_id={pair_id}' for arm in ('swarm','baseline')}}
            db.execute('INSERT INTO pairs VALUES(?,?)', (pair_id, encode(pair)))
            if key:
                db.execute('INSERT INTO idempotency VALUES(?,?,?)', (key, request_hash, pair_id))
            db.execute("INSERT INTO meta VALUES('active_pair',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (pair_id,))
            return pair, True

    def update(self, pair_id, **fields):
        if {'immutable_spec', 'spec_hash', 'swarm_run_id', 'baseline_run_id', 'pair_id'} & fields.keys():
            raise Rejected('IMMUTABLE_PAIR', '实验规格和两侧 run 身份不可变更')
        with self.tx() as db:
            pair = json.loads(db.execute('SELECT data FROM pairs WHERE id=?', (pair_id,)).fetchone()[0])
            pair.update(fields)
            db.execute('UPDATE pairs SET data=? WHERE id=?', (encode(pair), pair_id))
            return pair
