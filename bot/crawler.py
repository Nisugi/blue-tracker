import asyncio, discord, time
from datetime import datetime, timedelta, timezone
from .db import fetchone
from .repost import should_repost

REQ_PAUSE   = 2.5
PAGE_SIZE   = 50
CUTOFF_DAYS = 365 * 10
save_counter = 0

async def crawl_one(ch, cutoff, me, db, build_snippet, blue_ids, db_add_author, db_add_post):
    global save_counter
    if not ch.permissions_for(me).read_message_history: return

    row = await fetchone(db, "SELECT MAX(id) FROM posts WHERE chan_id = ?", (ch.id,))
    after = discord.Object(id=int(row[0])) if row and row[0] else None

    async for m in ch.history(limit=PAGE_SIZE, oldest_first=True, after=after):
        if m.created_at < cutoff:
            break
        if should_repost(m, blue_ids):
            blue_ids.add(m.author.id)
            await db_add_author(m.author)
            snippet = await build_snippet(m)
            await db_add_post(m, snippet)
            await db.commit()
            save_counter += 1

async def slow_crawl(src_guild, db, build_snippet, db_add_author, db_add_post, blue_ids):
    me = src_guild.get_member(src_guild._state.user.id) or await src_guild.fetch_member(src_guild._state.user.id)
    cutoff = datetime.utcnow().replace(tzinfo=timezone.utc) - timedelta(days=CUTOFF_DAYS)

    while True:
        for parent in src_guild.text_channels:
            await crawl_one(parent, cutoff, me, db, build_snippet, blue_ids, db_add_author, db_add_post)
            await asyncio.sleep(REQ_PAUSE)
            for th in iter_all_threads(parent):
                await crawl_one(th, cutoff, me, db, build_snippet, blue_ids, db_add_author, db_add_post)
                await asyncio.sleep(REQ_PAUSE)
        await asyncio.sleep(30)

async def iter_all_threads(parent: discord.TextChannel):
    """Yield active threads first, then archived public threads (oldest-first)."""
    for th in parent.threads:            # active
        yield th

    try:
        archived = [
            th async for th in parent.archived_threads(
                limit=None,      # newest-first
                private=False)
        ]
        for th in reversed(archived):    # oldest-first order
            yield th
    except discord.Forbidden:
        return