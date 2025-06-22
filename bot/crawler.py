import asyncio, discord, time
from datetime import datetime, timedelta, timezone
from .db import fetchone
from .repost import should_repost, cleanup_caches
from .config import REQ_PAUSE, PAGE_SIZE, CUTOFF_DAYS, CRAWL_VERBOSITY, IGNORED_CHANNELS

save_counter = 0
inaccessible_channels = set()  # Cache of channel IDs we can't access

async def crawl_one(ch, cutoff, me, db, build_snippet, blue_ids, db_add_author, db_add_post):
    """Crawl one channel or thread for messages"""
    global save_counter, inaccessible_channels
    
    if ch.id in IGNORED_CHANNELS: 
        return
    
    # Skip if we already know we can't access this channel
    if ch.id in inaccessible_channels:
        return
        
    if not ch.permissions_for(me).read_message_history: 
        inaccessible_channels.add(ch.id)
        print(f"[crawler] 🚫 No access to #{ch.name} (ID: {ch.id}) - caching for future skips")
        return

    row = await fetchone(db, "SELECT MAX(id) FROM posts WHERE chan_id = ?", (ch.id,))
    last_processed_id = int(row[0]) if row and row[0] else None
    after = discord.Object(id=last_processed_id) if last_processed_id else None
    
    pulled = 0
    saved_this_run = 0
    new_messages_found = 0
    highest_id_this_run = last_processed_id

    try:
        # Use timeout to prevent hanging on slow channels
        async def _get_messages():
            return [m async for m in ch.history(limit=PAGE_SIZE, oldest_first=True, after=after)]
        
        messages = await asyncio.wait_for(_get_messages(), timeout=15.0)
        
        for m in messages:
            if m.created_at < cutoff:
                break
            pulled += 1

            if highest_id_this_run is None or m.id > highest_id_this_run:
                highest_id_this_run = m.id

            existing = await fetchone(db, "SELECT id FROM posts WHERE id = ?", (m.id,))
            if existing:
                continue
                
            new_messages_found += 1            
            
            if should_repost(m, blue_ids):
                blue_ids.add(m.author.id)
                await db_add_author(m.author)
                snippet = await build_snippet(m)
                
                # Use INSERT OR IGNORE to handle duplicates gracefully
                await db.execute(
                    "INSERT INTO posts VALUES (?,?,?,?,?,?)",
                    (m.id, m.channel.id, m.author.id,
                     int(m.created_at.timestamp()*1000), snippet, 0)
                )
                
                save_counter += 1
                saved_this_run += 1
                
                await db.commit()
        
        # Show progress for this channel/thread
        ch_type = "thread" if isinstance(ch, discord.Thread) else "channel"
        if new_messages_found > 0:
            print(f"[crawler] #{ch.name:<30} ({ch_type:<7}) pulled={pulled:<3} new={new_messages_found:<3} saved={saved_this_run:<2} total={save_counter:<5}")
        elif pulled > 0:
            print(f"[crawler] #{ch.name:<30} ({ch_type:<7}) pulled={pulled:<3} (all duplicates) saved={saved_this_run:<2}")
        else:
            print(f"[crawler] #{ch.name:<30} ({ch_type:<7}) no new messages")
                
    except asyncio.TimeoutError:
        print(f"[crawler] ⚠️  TIMEOUT in #{ch.name} - skipping this pass")
    except discord.Forbidden:
        # No access to this channel - add to cache
        inaccessible_channels.add(ch.id)
        print(f"[crawler] 🚫 Forbidden access to #{ch.name} (ID: {ch.id}) - caching for future skips")
    except discord.HTTPException as e:
        if e.status == 403:  # Another form of forbidden
            inaccessible_channels.add(ch.id)
            print(f"[crawler] 🚫 HTTP 403 for #{ch.name} (ID: {ch.id}) - caching for future skips")
        elif 500 <= e.status < 600 or e.status == 429:
            print(f"[crawler] ⚠️  Skipping #{ch.name}: {e.status} {e.text or ''}".strip())
        else:
            print(f"[crawler] ❌ Error in #{ch.name}: {e}")
    except Exception as e:
        print(f"[crawler] ❌ Unexpected error in #{ch.name}: {e}")

async def iter_all_threads(parent: discord.TextChannel):
    """Yield active threads first, then archived public threads (oldest-first)."""
    # Active threads first
    for th in parent.threads:
        yield th

    # Then archived public threads
    try:
        archived = [
            th async for th in parent.archived_threads(
                limit=None,      # newest-first by default
                private=False    # public only
            )
        ]
        # Reverse to get oldest-first order
        for th in reversed(archived):
            yield th
    except discord.Forbidden:
        print(f"[crawler] No access to archived threads in #{parent.name}")
        return
    except discord.HTTPException as e:
        if 500 <= e.status < 600 or e.status == 429:
            print(f"[crawler] Skipping archived threads in #{parent.name}: "
                  f"{e.status} {e.text or ''}".strip())
            return
        raise

async def slow_crawl(src_guild, db, build_snippet, db_add_author, db_add_post, blue_ids, client):
    """Main crawler loop - runs continuously"""
    global inaccessible_channels
    
    me = src_guild.get_member(client.user.id) or await src_guild.fetch_member(src_guild._state.user.id)
    cutoff = datetime.utcnow().replace(tzinfo=timezone.utc) - timedelta(days=CUTOFF_DAYS)
    
    cleanup_counter = 0
    sweep_number = 0
    
    # Calculate accessible channels
    all_channels = [c for c in src_guild.text_channels if c.id not in IGNORED_CHANNELS]
    
    print(f"[crawler] Starting slow crawl with {CUTOFF_DAYS} day cutoff")
    print(f"[crawler] Total channels: {len(src_guild.text_channels)}, Non-ignored: {len(all_channels)}")

    while True:
        try:
            sweep_number += 1
            
            # Calculate accessible channels for this sweep
            accessible_channels = [c for c in all_channels if c.id not in inaccessible_channels]
            
            print(f"\n[crawler] 🔄 Starting sweep #{sweep_number} of {src_guild.name}")
            print(f"[crawler] 📊 Channels: {len(accessible_channels)} accessible, {len(inaccessible_channels)} cached as inaccessible")
            
            channels_processed = 0
            threads_processed = 0
            
            # Crawl all text channels
            for parent in src_guild.text_channels:
                if parent.id in IGNORED_CHANNELS:
                    continue
                    
                if parent.id in inaccessible_channels:
                    continue  # Skip silently
                    
                channels_processed += 1
                print(f"[crawler] 📁 Processing channel #{parent.name} ({channels_processed}/{len(accessible_channels)})")
                
                await crawl_one(parent, cutoff, me, db, build_snippet, blue_ids, db_add_author, db_add_post)
                await asyncio.sleep(REQ_PAUSE)
                
                # Count and crawl threads
                thread_count = 0
                async for th in iter_all_threads(parent):
                    # Skip if thread is in inaccessible cache
                    if th.id in inaccessible_channels:
                        continue
                        
                    thread_count += 1
                    threads_processed += 1
                    print(f"[crawler] 🧵 Processing thread #{th.name} (#{thread_count} in #{parent.name})")
                    await crawl_one(th, cutoff, me, db, build_snippet, blue_ids, db_add_author, db_add_post)
                    await asyncio.sleep(REQ_PAUSE)
                
                if thread_count > 0:
                    print(f"[crawler] ✅ Completed #{parent.name} - processed {thread_count} threads")
            
            print(f"[crawler] 🏁 Sweep #{sweep_number} complete: {channels_processed} channels, {threads_processed} threads, {save_counter} total messages saved")
            
            # Periodic cache cleanup
            cleanup_counter += 1
            if cleanup_counter % 50 == 0:  # Every 50 sweeps
                print(f"[crawler] 🧹 Running cache cleanup...")
                cleanup_caches()
                
            # Every 100 sweeps, clear the inaccessible cache to retry
            # This handles cases where permissions might have changed
            if sweep_number % 100 == 0:
                old_count = len(inaccessible_channels)
                inaccessible_channels.clear()
                print(f"[crawler] 🔄 Cleared inaccessible channel cache ({old_count} entries) to retry permissions")
                
        except Exception as e:
            print(f"[crawler] ❌ Error in main loop: {e}")
            await asyncio.sleep(10)  # Brief pause before retrying
        
        print(f"[crawler] 😴 Sleeping 30s before next sweep...")
        await asyncio.sleep(30)  # Half-minute break between full sweeps

def get_inaccessible_count():
    """Get count of cached inaccessible channels (for monitoring)"""
    return len(inaccessible_channels)

def clear_inaccessible_cache():
    """Manually clear the inaccessible channels cache"""
    global inaccessible_channels
    count = len(inaccessible_channels)
    inaccessible_channels.clear()
    return count
