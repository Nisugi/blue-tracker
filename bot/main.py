import discord, asyncio, time, signal, sys
from .config import (TOKEN, SOURCE_GUILD_ID, AGGREGATOR_GUILD_ID, CENTRAL_CHAN_ID,
                     REPLAY_MODE, SEED_BLUE_IDS, DB_PATH, API_PAUSE)
from .db import open_db, fetchone
from .repost import should_repost, repost_live, build_snippet
from .crawler import slow_crawl
from .github_backup import safe_github_backup

client = discord.Client()
db = None
blue_ids = set(SEED_BLUE_IDS)

# ── Helper functions ────────────────────────────────────────────────
async def db_add_post(m, snippet, already_replayed=False):
    """Add post to database"""
    await db.execute("INSERT OR IGNORE INTO posts VALUES (?,?,?,?,?,?)",
                     (m.id, m.channel.id, m.author.id,
                      int(m.created_at.timestamp()*1000), snippet,
                      1 if already_replayed else 0))

async def db_add_author(u):
    """Add or update author in database"""
    await db.execute("INSERT OR IGNORE INTO authors VALUES (?,?)",
                     (u.id, u.display_name or u.name))
    # Update name if it was previously NULL
    await db.execute(
        "UPDATE authors SET author_name = ? "
        "WHERE author_id = ? AND author_name IS NULL",
        (u.display_name or u.name, u.id)
    )

# ── One-time replay function ───────────────────────────────────────
async def replay_all(dst_guild):
    """Replay all unreplayed messages from database"""
    print("► Starting full replay …")
    
    progress_row = await fetchone(db, "SELECT COUNT(*) FROM posts WHERE replayed = 0")
    total_to_replay = progress_row[0] if progress_row else 0
    if total_to_replay == 0:
        print("► Nothing to replay.")
        return
        
    start_time = time.time()
    count = 0
    
    async with db.execute(
        "SELECT id, chan_id, author_id, content "
        "FROM posts "
        "WHERE replayed = 0 "
        "ORDER BY ts ASC"
    ) as cur:
        async for msg_id, chan_id, author_id, body in cur:
            try:
                # Get source channel
                src_ch = client.get_channel(int(chan_id))
                if src_ch is None:
                    try:
                        src_ch = await client.fetch_channel(int(chan_id))
                    except discord.NotFound:
                        print(f"[replay] Channel {chan_id} not found, skipping message {msg_id}")
                        await db.execute("UPDATE posts SET replayed = 1 WHERE id = ?", (msg_id,))
                        continue

                src_guild_name = src_ch.guild.name
                src_channel_name = src_ch.name

                # Get author info
                row = await fetchone(db, "SELECT author_name FROM authors WHERE author_id = ?", (author_id,))
                if row and row[0]:
                    display_name = row[0]
                    avatar = None  # Skip avatar fetch for performance
                else:
                    try:
                        user = client.get_user(author_id) or await client.fetch_user(author_id)
                        display_name = user.display_name if user else f"ID {author_id}"
                        avatar = user.display_avatar.url if user else None
                    except discord.NotFound:
                        display_name = f"ID {author_id}"
                        avatar = None

                # Build message content
                jump = f"https://discord.com/channels/{src_ch.guild.id}/{chan_id}/{msg_id}"
                snippet = body or "(embed/attachment only)"
                if len(snippet) > 200:
                    snippet = snippet[:197] + "…"

                full_content = (
                    f"{display_name} ({src_guild_name} • #{src_channel_name}):\n"
                    f"{snippet}\n{jump}"
                )

                # Send to mirror channels
                from .repost import ensure_mirror, get_webhook, safe_webhook_send
                
                mirror = await ensure_mirror(dst_guild, src_ch)
                is_thread = isinstance(mirror, discord.Thread)
                parent = mirror.parent if is_thread else mirror
                wh = await get_webhook(parent)
                
                kwargs = dict(
                    content=full_content,
                    username=display_name,
                    avatar_url=avatar,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                if is_thread:
                    kwargs["thread"] = mirror
                
                await safe_webhook_send(wh, **kwargs)
                
                # Mark as replayed
                await db.execute("UPDATE posts SET replayed = 1 WHERE id = ?", (msg_id,))
                await db.commit()

                count += 1
                elapsed = time.time() - start_time
                per_msg = elapsed / count
                eta = per_msg * (total_to_replay - count)

                print(f"[replay] {count}/{total_to_replay} "
                      f"({count / total_to_replay:.1%}) "
                      f"ETA: {eta/60:.1f} min "
                      f"Elapsed: {elapsed/60:.1f} min")
                
                await asyncio.sleep(API_PAUSE)
                
            except Exception as e:
                print(f"[replay] Error processing message {msg_id}: {e}")
                # Mark as replayed to avoid infinite retry
                await db.execute("UPDATE posts SET replayed = 1 WHERE id = ?", (msg_id,))
                await db.commit()

    print("► Replay complete")

# ── Graceful shutdown handling ─────────────────────────────────────
async def cleanup_on_exit():
    """Clean up resources on shutdown"""
    print("\n[Bot] Shutting down gracefully...")
    if db:
        await db.close()
    if not client.is_closed():
        await client.close()

def signal_handler(sig, frame):
    """Handle shutdown signals"""
    asyncio.create_task(cleanup_on_exit())
    sys.exit(0)

# ── Event handlers ─────────────────────────────────────────────────
@client.event
async def on_ready():
    global db
    
    try:
        db = await open_db()
        
        # Add replayed column if it doesn't exist
        try:
            await db.execute("ALTER TABLE posts ADD COLUMN replayed INTEGER DEFAULT 0")
            print("[DB] Added 'replayed' column to posts table.")
        except Exception as e:
            if "duplicate column name" in str(e).lower():
                print("[DB] 'replayed' column already exists.")
            else:
                raise

        # Initialize blue_ids with seed data
        blue_ids.update(SEED_BLUE_IDS)
        for uid in SEED_BLUE_IDS:
            await db.execute("INSERT OR IGNORE INTO authors VALUES (?, ?)", (uid, None))
        await db.commit()
        
        print(f"[Self-Bot] Logged in as {client.user} ({client.user.id})")

        # Wait for guilds to be available
        src_guild = client.get_guild(SOURCE_GUILD_ID)
        dst_guild = client.get_guild(AGGREGATOR_GUILD_ID)
        while not src_guild or not dst_guild:
            await asyncio.sleep(1)
            src_guild = client.get_guild(SOURCE_GUILD_ID)
            dst_guild = client.get_guild(AGGREGATOR_GUILD_ID)

        # Show database stats
        row = await fetchone(db, "SELECT COUNT(*) FROM posts")
        total = row[0] if row else 0
        print(f"[DB] posts table currently holds {total:,} rows.")

        # Start background tasks
        asyncio.create_task(safe_github_backup("startup"))

        if not REPLAY_MODE:
            # Start the crawler
            asyncio.create_task(slow_crawl(src_guild, db, build_snippet, 
                                         db_add_author, db_add_post, blue_ids, client))
        else:
            # Start replay mode, then start crawler when done
            print("[Bot] REPLAY_MODE enabled - starting replay...")
            async def replay_then_crawl():
                await replay_all(dst_guild)
                print("[Bot] Replay complete, starting crawler...")
                await slow_crawl(src_guild, db, build_snippet, 
                               db_add_author, db_add_post, blue_ids, client)
            
            asyncio.create_task(replay_then_crawl())
            
    except Exception as e:
        print(f"[Bot] Error in on_ready: {e}")
        raise

@client.event
async def on_message(m: discord.Message):
    """Handle new messages from source guild"""
    if m.author.bot or m.author.id == client.user.id:
        return
    if m.guild and m.guild.id != SOURCE_GUILD_ID:
        return
    if not should_repost(m, blue_ids):
        return

    try:
        dst_guild = client.get_guild(AGGREGATOR_GUILD_ID)
        await repost_live(m, dst_guild, client)

        # Store live post in database
        blue_ids.add(m.author.id)
        await db_add_author(m.author)
        snippet = await build_snippet(m)
        await db_add_post(m, snippet, already_replayed=True)
        await db.commit()
        
    except Exception as e:
        print(f"[Bot] Error processing message {m.id}: {e}")

@client.event  
async def on_error(event, *args, **kwargs):
    """Handle uncaught exceptions"""
    import traceback
    print(f"[Bot] Error in event {event}:")
    traceback.print_exc()

# ── Setup signal handlers and run ─────────────────────────────────
if __name__ == "__main__":
    # Setup graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Run the bot
    try:
        client.run(TOKEN)
    except KeyboardInterrupt:
        print("\n[Bot] Received keyboard interrupt")
    except Exception as e:
        print(f"[Bot] Fatal error: {e}")
    finally:
        asyncio.run(cleanup_on_exit())