import aiosqlite, re
from .config import DB_PATH
DIGITS_ONLY = re.compile(r'^#?\d+$')

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
CREATE TABLE IF NOT EXISTS gm_names(
  author_id TEXT PRIMARY KEY,
  gm_name TEXT NOT NULL,
  notes TEXT
);
CREATE TABLE IF NOT EXISTS channels (
  chan_id     TEXT PRIMARY KEY,
  name        TEXT,
 -- parent_id   TEXT,
  accessible  INTEGER NOT NULL DEFAULT 1       -- 1 = visible, 0 = no-access
);

CREATE TABLE IF NOT EXISTS crawl_progress (     -- keeps “last-seen” id
  chan_id      TEXT PRIMARY KEY,
  last_seen_id INTEGER NOT NULL,
  updated_at   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_posts_chan_id ON posts(chan_id);
CREATE INDEX IF NOT EXISTS idx_posts_author_id ON posts(author_id);
CREATE INDEX IF NOT EXISTS idx_posts_ts ON posts(ts);
CREATE INDEX IF NOT EXISTS idx_posts_replayed ON posts(replayed);
CREATE INDEX IF NOT EXISTS idx_channels_name   ON channels(name);
-- CREATE INDEX IF NOT EXISTS idx_channels_parent ON channels(parent_id);
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

async def get_gm_display_name(db, author_id, fallback_name):
    """Get proper GM name with override priority: gm_names table > config override > fallback"""
    from .config import GM_NAME_OVERRIDES
    
    # First check database override table
    row = await fetchone(db, "SELECT gm_name FROM gm_names WHERE author_id = ?", (author_id,))
    if row and row[0]:
        return row[0]
    
    # Then check config overrides
    if author_id in GM_NAME_OVERRIDES:
        return GM_NAME_OVERRIDES[author_id]
    
    # Fall back to provided name
    return fallback_name  

async def ensure_parent_column(db):
    """
    Add parent_id column + index exactly once.
    Safe to call at every startup.
    """
    # 1) fetch current columns
    rows = await fetchall(db, "PRAGMA table_info(channels)")
    if any(r[1] == 'parent_id' for r in rows):    # r[1] is name
        return  # already migrated

    print("[DB] Adding parent_id column to channels …")
    await execute_with_retry(
        db, "ALTER TABLE channels ADD COLUMN parent_id TEXT")
    await execute_with_retry(
        db, "CREATE INDEX IF NOT EXISTS idx_channels_parent ON channels(parent_id)")
    await db.commit()
    print("[DB] parent_id column added successfully")

async def backfill_channel_names(db, client):
    # ➊ everything that is null / empty **or** looks like an ID
    rows = await db.execute_fetchall(
        """
        SELECT chan_id, COALESCE(name,'')            -- name may be NULL
        FROM   channels
        WHERE  name IS NULL 
           OR  name = ''
           OR  name GLOB '[0-9]*'                    -- all digits? (SQLite)
           OR  name GLOB '#[0-9]*'
        """
    )

    print(f"[names] filling {len(rows)} missing channel/thread names …")

    for cid, current in rows:
        # (extra safety when running on older SQLite that lacks GLOB)
        if current and not DIGITS_ONLY.fullmatch(current):
            continue

        try:
            ch = await client.fetch_channel(int(cid))
            await execute_with_retry(
                db,
                "UPDATE channels SET name = ? WHERE chan_id = ?",
                (ch.name, cid)
            )
        except discord.Forbidden:
            # bot can’t see it – leave unchanged
            pass
        except Exception as e:
            print(f"[names] {cid}: {e}")

    await db.commit()

async def cleanse_numeric_placeholders(db):
    """
    • channels.name → NULL if it’s '', all-digits, or '#123…'
    • posts.chan_id → NULL if it’s all digits            (old crawl artefact)
    """
    await execute_with_retry(db, """
        UPDATE channels
           SET name = NULL
         WHERE name IS NULL
            OR name = ''
            OR name GLOB '[0-9]*'
            OR name GLOB '#[0-9]*'
    """)
    await execute_with_retry(db, """
        UPDATE posts
           SET chan_id = NULL
         WHERE chan_id GLOB '[0-9]*'
    """)
    await db.commit()
