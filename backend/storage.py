"""Local SQLite records; failed/stopped assistant output stays out of model history."""
import json
import sqlite3
import uuid
from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, title TEXT, model TEXT, mode TEXT,
                    framework TEXT, framework_hash TEXT, created_at TEXT);
                CREATE TABLE IF NOT EXISTS turns (
                    id TEXT PRIMARY KEY, session_id TEXT REFERENCES sessions(id),
                    created_at TEXT, status TEXT, payload TEXT);
                CREATE INDEX IF NOT EXISTS turns_session ON turns(session_id);
                CREATE TABLE IF NOT EXISTS session_routes (
                    session_id TEXT PRIMARY KEY REFERENCES sessions(id), payload TEXT);
            """)

    def connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def create_session(self, model, mode, framework, framework_hash):
        item = dict(id=str(uuid.uuid4()), title="New conversation", model=model, mode=mode,
                    framework=framework, framework_hash=framework_hash, created_at=now())
        with self.connect() as db:
            db.execute("INSERT INTO sessions VALUES (:id,:title,:model,:mode,:framework,:framework_hash,:created_at)", item)
        return item

    def sessions(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT id,title,model,mode,framework_hash,created_at FROM sessions ORDER BY created_at DESC")]

    def session(self, sid):
        with self.connect() as db:
            row = db.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
        return dict(row) if row else None

    def turns(self, sid):
        with self.connect() as db:
            rows = db.execute("SELECT payload FROM turns WHERE session_id=? ORDER BY rowid", (sid,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def route(self, sid):
        with self.connect() as db:
            row = db.execute('SELECT payload FROM session_routes WHERE session_id=?', (sid,)).fetchone()
        return json.loads(row[0]) if row else None

    def save_route(self, sid, route):
        with self.connect() as db:
            db.execute('INSERT INTO session_routes VALUES (?,?) ON CONFLICT(session_id) DO UPDATE SET payload=excluded.payload',
                       (sid, json.dumps(route)))

    def save_turn(self, sid, turn):
        with self.connect() as db:
            db.execute("INSERT INTO turns VALUES (?,?,?,?,?)", (
                turn["id"], sid, turn["created_at"], turn["status"], json.dumps(turn, ensure_ascii=False)))
            db.execute("UPDATE sessions SET title=? WHERE id=? AND title='New conversation'",
                       (turn["prompt"][:70], sid))

    def fork_session(self, sid, framework, framework_hash):
        """Upgrade by copying within one transaction; never rewrite a pinned chat."""
        with self.connect() as db:
            session = dict(db.execute('SELECT * FROM sessions WHERE id=?', (sid,)).fetchone())
            session.update(id=str(uuid.uuid4()), framework=framework, framework_hash=framework_hash,
                           created_at=now(), title=session['title'] + ' · word stream')
            db.execute('INSERT INTO sessions VALUES (:id,:title,:model,:mode,:framework,:framework_hash,:created_at)', session)
            rows = db.execute('SELECT payload FROM turns WHERE session_id=? ORDER BY rowid', (sid,)).fetchall()
            for row in rows:
                turn = json.loads(row[0])
                turn['source_turn_id'] = turn['id']
                turn['id'] = str(uuid.uuid4())
                turn.setdefault('protocol', 'MC/1')
                db.execute('INSERT INTO turns VALUES (?,?,?,?,?)', (
                    turn['id'], session['id'], turn['created_at'], turn['status'], json.dumps(turn, ensure_ascii=False)))
        return session
