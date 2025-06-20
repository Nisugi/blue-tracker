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
CREATE INDEX IF NOT EXISTS idx_posts_chan_id ON posts(chan_id);
CREATE INDEX IF NOT EXISTS idx_posts_author_id ON posts(author_id);
CREATE INDEX IF NOT EXISTS idx_posts_ts ON posts(ts);
CREATE INDEX IF NOT EXISTS idx_posts_replayed ON posts(replayed);
"""

async def open_db():
    """Open database connection and create tables"""
    try:
        # Ensure the data directory exists
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        
        db = await aiosqlite.connect(DB_PATH)
        await db.executescript(CREATE_SQL)
        
        # Enable foreign keys and optimize settings
        await db.execute("PRAGMA foreign_keys = ON")
        await db.execute("PRAGMA synchronous = NORMAL")
        await db.execute("PRAGMA cache_size = 10000")
        await db.execute("PRAGMA temp_store = MEMORY")
        
        return db
    except Exception as e:
        print(f"[DB] Error opening database: {e}")
        raise

async def fetchone(db, query, params=()):
    """Execute query and fetch one result"""
    try:
        cur = await db.execute(query, params)
        row = await cur.fetchone()
        await cur.close()
        return row
    except Exception as e:
        print(f"[DB] Error in fetchone: {e}")
        raise

async def fetchall(db, query, params=()):
    """Execute query and fetch all results"""
    try:
        cur = await db.execute(query, params)
        rows = await cur.fetchall()
        await cur.close()
        return rows
    except Exception as e:
        print(f"[DB] Error in fetchall: {e}")
        raise

async def execute_with_retry(db, query, params=(), max_retries=3):
    """Execute query with retry logic for database locks"""
    import asyncio
    
    for attempt in range(max_retries):
        try:
            cursor = await db.execute(query, params)
            return cursor
        except aiosqlite.OperationalError as e:
            if "database is locked" in str(e).lower() and attempt < max_retries - 1:
                print(f"[DB] Database locked, retrying in {0.1 * (2 ** attempt)}s...")
                await asyncio.sleep(0.1 * (2 ** attempt))  # Exponential backoff
                continue
            else:
                print(f"[DB] Database error after {attempt + 1} attempts: {e}")
                raise
        except Exception as e:
            print(f"[DB] Unexpected error: {e}")
            raise

async def get_db_stats(db):
    """Get database statistics"""
    try:
        stats = {}
        
        # Get table sizes
        stats['posts_count'] = (await fetchone(db, "SELECT COUNT(*) FROM posts"))[0]
        stats['authors_count'] = (await fetchone(db, "SELECT COUNT(*) FROM authors"))[0]
        stats['unreplayed_count'] = (await fetchone(db, "SELECT COUNT(*) FROM posts WHERE replayed = 0"))[0]
        
        # Get database file size
        stats['db_size_mb'] = DB_PATH.stat().st_size / (1024 * 1024) if DB_PATH.exists() else 0
        
        return stats
    except Exception as e:
        print(f"[DB] Error getting stats: {e}")
        return {}

async def cleanup_old_posts(db, days_to_keep=365):
    """Remove posts older than specified days"""
    try:
        import time
        cutoff_ts = int((time.time() - (days_to_keep * 24 * 60 * 60)) * 1000)
        
        cursor = await db.execute("DELETE FROM posts WHERE ts < ?", (cutoff_ts,))
        deleted_count = cursor.rowcount
        await db.commit()
        
        print(f"[DB] Cleaned up {deleted_count} old posts")
        return deleted_count
    except Exception as e:
        print(f"[DB] Error cleaning up old posts: {e}")
        return 0