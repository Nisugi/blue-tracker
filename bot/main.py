import discord, asyncio, time
from .config import (TOKEN, SOURCE_GUILD_ID, AGGREGATOR_GUILD_ID, CENTRAL_CHAN_ID,
                     REPLAY_MODE, SEED_BLUE_IDS, DB_PATH)
from .db import open_db, fetchone
from .repost import ensure_mirror, get_webhook, should_repost
from .crawler import slow_crawl
from .github_backup import github_backup

client = discord.Client(intents=discord.Intents.none())
db = None
blue_ids = set(SEED_BLUE_IDS)

# ── helpers ---------------------------------------------------------
async def db_add_post(m, snippet, already_replayed: bool):
    await db.execute("INSERT OR IGNORE INTO posts VALUES (?,?,?,?,?,?)",
                     (m.id, m.channel.id, m.author.id,
                      int(m.created_at.timestamp()*1000), snippet,
                      1 if already_replayed else 0))

async def db_add_author(u):
    await db.execute("INSERT OR IGNORE INTO authors VALUES (?,?)",
                     (u.id, u.display_name or u.name))

async def build_snippet(msg: discord.Message) -> str:
    txt = msg.content or "(embed/attachment only)"
    return (txt[:197] + "…") if len(txt) > 200 else txt

# ── event handlers --------------------------------------------------
@client.event
async def on_ready():
    global db
    db = await open_db()

    src = client.get_guild(SOURCE_GUILD_ID)
    dst = client.get_guild(AGGREGATOR_GUILD_ID)
    while not (src and dst):
        await asyncio.sleep(1)
        src, dst = client.get_guild(SOURCE_GUILD_ID), client.get_guild(AGGREGATOR_GUILD_ID)

    # backup db to github using github_backup.py
    # asyncio.to_thread(github_backup, "startup")
    if not REPLAY_MODE:
        asyncio.create_task(slow_crawl(src, db, build_snippet, db_add_author, db_add_post, blue_ids))

@client.event
async def on_message(m: discord.Message):
    if m.author.bot or m.author.id == client.user.id: return
    if m.guild and m.guild.id != SOURCE_GUILD_ID:     return
    if not should_repost(m, blue_ids):                return

    dst_guild = client.get_guild(AGGREGATOR_GUILD_ID)
    snippet   = await build_snippet(m)

    mirror = await ensure_mirror(dst_guild, m.channel)
    wh     = await get_webhook(mirror if isinstance(mirror, discord.TextChannel) else mirror.parent)
    await wh.send(content=f"{m.author.display_name}: {snippet}",
                  username=m.author.display_name,
                  avatar_url=m.author.display_avatar.url,
                  thread=mirror if isinstance(mirror, discord.Thread) else None)

    blue_ids.add(m.author.id)
    await db_add_author(m.author)
    await db_add_post(m, snippet, already_replayed=True)
    await db.commit()

# ── run -------------------------------------------------------------
client.run(TOKEN)
