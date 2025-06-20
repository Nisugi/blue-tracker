import aiosqlite
from .config import DB_PATH

CREATE_SQL = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS posts (
  id        INTEGER PRIMARY KEY,
  chan_id   TEXT,
  author_id TEXT,
  ts        INTEGER,
  content   TEXT,
  replayed  INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS authors(
  author_id TEXT PRIMARY KEY,
  author_name TEXT
);
"""

async def open_db():
    db = await aiosqlite.connect(DB_PATH)
    await db.executescript(CREATE_SQL)
    return db

async def fetchone(db, query, params=()):
    cur = await db.execute(query, params)
    row = await cur.fetchone()
    await cur.close()
    return row
