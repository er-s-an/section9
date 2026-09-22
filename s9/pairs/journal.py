import json
import sqlite3
from contextlib import contextmanager

from s9.store import encode, now


class PairJournal:
    """Rebuildable index of committed arm events, never execution authority."""
    def __init__(self, path):
        self.path = path
        with self.tx() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS journal(sequence INTEGER PRIMARY KEY AUTOINCREMENT,
              pair_id TEXT NOT NULL,arm TEXT NOT NULL,run_id TEXT NOT NULL,run_sequence INTEGER NOT NULL,
              event_id TEXT NOT NULL,data TEXT NOT NULL,UNIQUE(run_id,run_sequence,event_id));
              CREATE TABLE IF NOT EXISTS cursors(run_id TEXT PRIMARY KEY,sequence INTEGER NOT NULL);''')

    @contextmanager
    def tx(self):
        with sqlite3.connect(self.path, timeout=15) as db:
            db.execute('PRAGMA journal_mode=WAL')
            yield db

    def ingest(self, pair, arm, store):
        rid = pair[arm + '_run_id']
        with self.tx() as db:
            row = db.execute('SELECT sequence FROM cursors WHERE run_id=?', (rid,)).fetchone()
            cursor = row[0] if row else 0
        # Arm source is durable; retrying the same batch is safe after a crash.
        events = store.events(after=cursor, limit=10000)
        with self.tx() as db:
            for event in events:
                cursor = max(cursor, int(event['sequence']))
                if (event.get('run_id') != rid or any(
                    event.get(key) is not None and event[key] != expected
                    for key, expected in {'pair_id': pair['pair_id'], 'arm': arm,
                                          'spec_hash': pair['spec_hash']}.items()
                )):
                    continue
                item = {**event, 'schema_version': '1', 'scope': 'run', 'pair_id': pair['pair_id'], 'arm': arm,
                        'run_sequence': int(event['sequence']), 'recorded_at': now(),
                        'evidence_ids': event['payload'].get('evidence_ids', []),
                        'config_revision': event.get('config_revision'), 'config_hash': event.get('config_hash'),
                        'stage': None, 'action': event['event_type'], 'result': event['payload'].get('summary')}
                cur = db.execute('INSERT OR IGNORE INTO journal(pair_id,arm,run_id,run_sequence,event_id,data) VALUES(?,?,?,?,?,?)',
                                 (pair['pair_id'], arm, rid, item['run_sequence'], item['event_id'], encode(item)))
                if cur.rowcount:
                    item['sequence'] = cur.lastrowid
                    db.execute('UPDATE journal SET data=? WHERE sequence=?', (encode(item), cur.lastrowid))
            db.execute('INSERT INTO cursors VALUES(?,?) ON CONFLICT(run_id) DO UPDATE SET sequence=MAX(sequence,excluded.sequence)', (rid, cursor))

    def read(self, pair_id, arm=None, after=0, limit=10000):
        with self.tx() as db:
            where, params = 'pair_id=? AND sequence>?', [pair_id, after]
            if arm:
                where += ' AND arm=?'
                params.append(arm)
            return [json.loads(r[0]) for r in db.execute(f'SELECT data FROM journal WHERE {where} ORDER BY sequence LIMIT ?', (*params, limit))]

    def cursor(self, pair_id):
        with self.tx() as db:
            return db.execute('SELECT COALESCE(MAX(sequence),0) FROM journal WHERE pair_id=?', (pair_id,)).fetchone()[0]
