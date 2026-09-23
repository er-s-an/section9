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
        # Fix one source high-water mark first.  Reading until an empty page is
        # racy when workers are still emitting events and can starve forever.
        with sqlite3.connect(store.path) as source_db:
            upper = int(source_db.execute('SELECT COALESCE(MAX(sequence),0) FROM events').fetchone()[0])
        # Arm source is durable; retrying the same batch is safe after a crash.
        while cursor < upper:
            events = store.events(after=cursor, limit=10000)
            if not events:
                break
            with self.tx() as db:
                for event in events:
                    sequence = int(event['sequence'])
                    if sequence > upper:
                        continue
                    cursor = max(cursor, sequence)
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

    def read_page(self, pair_id, arm=None, after=0, limit=200, watermark=None):
        """Read a stable page bounded by the journal sequence watermark."""
        with self.tx() as db:
            where, params = 'pair_id=? AND sequence>?', [pair_id, int(after)]
            if arm:
                where += ' AND arm=?'; params.append(arm)
            if watermark is not None:
                where += ' AND sequence<=?'; params.append(int(watermark))
            rows = db.execute(f'SELECT data FROM journal WHERE {where} ORDER BY sequence LIMIT ?', (*params, int(limit))).fetchall()
            return [json.loads(r[0]) for r in rows]

    def read_before(self, pair_id, arm, before, limit=200, watermark=None):
        """Read the page immediately before a sequence, within a fixed watermark."""
        with self.tx() as db:
            where = 'pair_id=? AND arm=? AND sequence<?'
            params = [pair_id, arm, int(before)]
            if watermark is not None:
                where += ' AND sequence<=?'; params.append(int(watermark))
            rows = db.execute(
                f'SELECT data FROM journal WHERE {where} ORDER BY sequence DESC LIMIT ?',
                (*params, int(limit)),
            ).fetchall()
            return [json.loads(row[0]) for row in reversed(rows)]

    def has_before(self, pair_id, arm, before, watermark=None):
        with self.tx() as db:
            where = 'pair_id=? AND arm=? AND sequence<?'
            params = [pair_id, arm, int(before)]
            if watermark is not None:
                where += ' AND sequence<=?'; params.append(int(watermark))
            return db.execute(f'SELECT 1 FROM journal WHERE {where} LIMIT 1', params).fetchone() is not None

    def read_by_ids(self, pair_id, arm, run_id, event_ids):
        ids = list(dict.fromkeys(str(event_id) for event_id in event_ids if event_id))
        if not ids:
            return []
        placeholders = ','.join('?' for _ in ids)
        with self.tx() as db:
            rows = db.execute(
                f'SELECT data FROM journal WHERE pair_id=? AND arm=? AND run_id=? AND event_id IN ({placeholders}) ORDER BY sequence',
                (pair_id, arm, run_id, *ids),
            ).fetchall()
            return [json.loads(row[0]) for row in rows]

    def count_after(self, pair_id, arm, after, watermark):
        with self.tx() as db:
            return int(db.execute(
                'SELECT COUNT(*) FROM journal WHERE pair_id=? AND arm=? AND sequence>? AND sequence<=?',
                (pair_id, arm, int(after), int(watermark)),
            ).fetchone()[0])

    def read_all(self, pair_id, arm=None, *, watermark=None, page_size=10000):
        """Read every row through one fixed journal watermark."""
        bound = self.watermark(pair_id, arm) if watermark is None else int(watermark)
        rows, after = [], 0
        while after < bound:
            page = self.read_page(pair_id, arm, after, page_size, bound)
            if not page:
                break
            rows.extend(page)
            after = int(page[-1]['sequence'])
        return rows

    def watermark(self, pair_id, arm=None):
        with self.tx() as db:
            if arm:
                return int(db.execute('SELECT COALESCE(MAX(sequence),0) FROM journal WHERE pair_id=? AND arm=?', (pair_id, arm)).fetchone()[0])
            return int(db.execute('SELECT COALESCE(MAX(sequence),0) FROM journal WHERE pair_id=?', (pair_id,)).fetchone()[0])

    def cursor(self, pair_id):
        with self.tx() as db:
            return db.execute('SELECT COALESCE(MAX(sequence),0) FROM journal WHERE pair_id=?', (pair_id,)).fetchone()[0]
