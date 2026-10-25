"""Conexão SQLite, transações e migrations."""
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from flask import current_app, g

MIGRATIONS_DIR = Path(__file__).resolve().parent / 'migrations'


def now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def today():
    return datetime.now().strftime('%Y-%m-%d')


def connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None, detect_types=0, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    conn.execute('PRAGMA busy_timeout = 5000')
    if path != ':memory:':
        conn.execute('PRAGMA journal_mode = WAL')
    return conn


def get_db():
    if 'db' not in g:
        g.db = connect(current_app.config['DATABASE_PATH'])
    return g.db


def close_db(_exc=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()


@contextmanager
def transaction(db=None):
    """Transação explícita. BEGIN IMMEDIATE bloqueia escrita concorrente durante
    a verificação de estoque, evitando venda acima do disponível."""
    db = db or get_db()
    if db.in_transaction:            # transação aninhada: participa da externa
        yield db
        return
    db.execute('BEGIN IMMEDIATE')
    try:
        yield db
        db.execute('COMMIT')
    except BaseException:
        db.execute('ROLLBACK')
        raise


def migrate(db):
    db.execute('CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)')
    done = {r['version'] for r in db.execute('SELECT version FROM schema_migrations')}
    applied = []
    for f in sorted(MIGRATIONS_DIR.glob('*.sql')):
        if f.stem in done:
            continue
        db.executescript('BEGIN;' + f.read_text(encoding='utf-8') +
                         f"\nINSERT INTO schema_migrations VALUES ('{f.stem}', '{now()}');\nCOMMIT;")
        applied.append(f.stem)
    return applied


def query(sql, params=(), one=False):
    cur = get_db().execute(sql, params)
    return cur.fetchone() if one else cur.fetchall()


def scalar(sql, params=(), default=0):
    row = get_db().execute(sql, params).fetchone()
    return default if row is None or row[0] is None else row[0]
