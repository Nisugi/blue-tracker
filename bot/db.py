import aiosqlite, re, time, asyncio, shutil
from .config import DB_PATH, REQ_PAUSE
from pathlib import Path
from discord import TextChannel, ForumChannel
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
  parent_id   TEXT,
  accessible  INTEGER NOT NULL DEFAULT 1       -- 1 = visible, 0 = no-access
);
CREATE TABLE IF NOT EXISTS crawl_progress (     -- keeps “last-seen” id
  chan_id      TEXT PRIMARY KEY,
  last_seen_id INTEGER NOT NULL,
  updated_at   INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS bot_metadata (
  key        TEXT PRIMARY KEY,
  value      TEXT,
  updated_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_posts_chan_id ON posts(chan_id);
CREATE INDEX IF NOT EXISTS idx_posts_author_id ON posts(author_id);
CREATE INDEX IF NOT EXISTS idx_posts_ts ON posts(ts);
CREATE INDEX IF NOT EXISTS idx_posts_replayed ON posts(replayed);
CREATE INDEX IF NOT EXISTS idx_channels_name   ON channels(name);
CREATE INDEX IF NOT EXISTS idx_channels_parent ON channels(parent_id);
"""

async def open_db():
    """Open database connection and create tables"""
    try:
        # Ensure the data directory exists
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        
        SEED_PATH = Path("/app/bluetracker_seed.db")   # inside the image
        if not DB_PATH.exists() and SEED_PATH.exists():
            print("[DB] No database on volume – seeding from image copy")
            DB_PATH.write_bytes(SEED_PATH.read_bytes())   # or shutil.copy

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
    # Fixed: Use fetchall and access by index, not name
    rows = await fetchall(db, 
        """
        SELECT chan_id, COALESCE(name,'') as name
        FROM   channels
        WHERE  name IS NULL 
           OR  name = ''
           OR  name = '#'
           OR  name GLOB '[0-9]*'
           OR  name GLOB '#[0-9]*'
        """
    )

    print(f"[names] filling {len(rows)} missing channel/thread names …")

    for row in rows:
        cid, current = row[0], row[1]  # Access by index for tuples
        
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
        except discord.Forbidden:  # Now this import exists
            # bot can't see it – leave unchanged
            pass
        except Exception as e:
            print(f"[names] {cid}: {e}")

    await db.commit()

async def prime_channel_table(db, guild):
    """Seed `channels` with names + parent_id, respecting rate-limits."""
    # keep an in-memory set so we don't hammer the same forbidden channels
    inaccessible_channels: set[str] = set()
    async def upsert(ch, parent_id=None, accessible=True):
        await execute_with_retry(
            db,
            """INSERT OR REPLACE INTO channels
               (chan_id, name, parent_id, accessible)
               VALUES (?, ?, ?, ?)""",
            (str(ch.id), ch.name, str(parent_id) if parent_id else None,
             1 if accessible else 0)
        )

    print("[prime] starting channel table seeding")

    text_like_channels = [
        c for c in guild.channels
        if isinstance(c, (TextChannel, ForumChannel))
    ]

    for ch in text_like_channels:
        await upsert(ch)                       # top-level itself

        # active threads (zero-cost, cached)
        for th in ch.threads:
            await upsert(th, parent_id=ch.id)

        # archived public threads → **one API hit per parent**
        try:
            async for th in ch.archived_threads(private=False):
                await upsert(th, parent_id=ch.id)
        except discord.Forbidden:
            # we can see the channel but not its history – remember & skip
            inaccessible_channels.add(ch.id)
            inaccessible_channels.add(str(ch.id))
            await upsert(ch, accessible=False)
        finally:
            # ★ give Discord a breather no matter what
            await asyncio.sleep(REQ_PAUSE)

    await db.commit()
    print(f"[prime] done – seeded {len(text_like_channels)} channels (+ threads)")

async def fix_channel_names_on_startup(db, client, src_guild):
    """Fix channel names during bot startup - runs with existing db connection"""
    print("[Startup] Checking for channel name issues...")
    
    # Check if we've already done this fix
    fix_check = await fetchone(db, "SELECT value FROM bot_metadata WHERE key = 'channel_fix_v1'")
    if fix_check:
        print("[Startup] Channel names already fixed, skipping")
        return
    
    # Find problematic channel names
    problematic = await fetchall(db, """
        SELECT DISTINCT chan_id, name
        FROM channels
        WHERE name IS NULL 
           OR name = ''
           OR name GLOB '[0-9]*'
           OR name GLOB '#[0-9]*'
        ORDER BY chan_id
    """)
    
    if not problematic:
        print("[Startup] No problematic channel names found")
        # Mark as complete
        await db.execute(
            "INSERT OR REPLACE INTO bot_metadata (key, value, updated_at) VALUES (?, ?, ?)",
            ('channel_fix_v1', 'completed', int(time.time()))
        )
        await db.commit()
        return
    
    print(f"[Startup] Found {len(problematic)} channels with problematic names, fixing...")
    
    fixed = 0
    failed = 0
    
    for chan_id, current_name in problematic:
        try:
            # Try to get from cache first
            channel = client.get_channel(int(chan_id))
            if not channel:
                channel = await client.fetch_channel(int(chan_id))
            
            if channel and channel.name:
                # Determine parent_id for threads
                parent_id = None
                if isinstance(channel, discord.Thread):
                    parent_id = str(channel.parent_id)
                
                await db.execute("""
                    UPDATE channels 
                    SET name = ?, parent_id = ?
                    WHERE chan_id = ?
                """, (channel.name, parent_id, chan_id))
                
                fixed += 1
                if fixed % 10 == 0:
                    print(f"[Startup] Progress: {fixed} channels fixed...")
                    await db.commit()  # Commit periodically
                
            else:
                # Mark as inaccessible
                await db.execute("""
                    UPDATE channels 
                    SET name = ?, accessible = 0
                    WHERE chan_id = ?
                """, (f"deleted-{chan_id}", chan_id))
                failed += 1
                
        except discord.Forbidden:
            await db.execute("""
                UPDATE channels 
                SET name = ?, accessible = 0
                WHERE chan_id = ?
            """, (f"no-access-{chan_id}", chan_id))
            failed += 1
        except Exception as e:
            print(f"[Startup] Error fixing channel {chan_id}: {e}")
            failed += 1
        
        # Be nice to Discord API
        if (fixed + failed) % 5 == 0:
            await asyncio.sleep(1)
    
    # Final commit
    await db.commit()
    
    # Mark fix as complete
    await db.execute(
        "INSERT OR REPLACE INTO bot_metadata (key, value, updated_at) VALUES (?, ?, ?)",
        ('channel_fix_v1', 'completed', int(time.time()))
    )
    await db.commit()
    
    print(f"[Startup] Channel fix complete: {fixed} fixed, {failed} failed")

async def ensure_bot_metadata_columns(db):
    """
    Make sure bot_metadata has   key, value, updated_at.
    Runs safely every start-up.
    """
    rows = await fetchall(db, "PRAGMA table_info(bot_metadata)")
    if not rows:
        # table didn’t exist (first run) – CREATE_SQL has correct schema already
        return

    if not any(col[1] == "updated_at" for col in rows):   # col[1] = column name
        print("[DB] Adding updated_at column to bot_metadata …")
        await execute_with_retry(
            db, "ALTER TABLE bot_metadata ADD COLUMN updated_at INTEGER")
        await db.commit()
