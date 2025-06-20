###############################################################################
# GSIV BLUE-TRACKER  ·  live relay + slow crawler + optional full replay
###############################################################################
# NOTES
# • Works with discord.py-self (self-bot) – no explicit Intents needed.
# • Stores posts in /data/bluetracker.db   (survives container restarts).
# • Slow crawl: 1 history request every ~2.5 s  (≈24 req/min — safe).
# • To do a one-time replay, set  REPLAY_MODE = True  and restart the add-on.
###############################################################################

import discord, asyncio, aiosqlite, json, os
from datetime import datetime, timedelta, timezone
from pathlib import Path

###############################################################################
# SEND DB TO GITHUB
###############################################################################
import requests, base64, time


REPO = "Nisugi/GSIV-BlueTracker"
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN") # github account settings -> developer settings -> fine-grained token  (Scopes: Contents: read & write only)
if not GITHUB_TOKEN:
    raise RuntimeError("GITHUB_TOKEN not set - fly secrets set GITHUB_TOKEN=...")
BRANCH = "main"

def github_backup(label="auto"):
    ts   = time.strftime("%Y%m%d-%H%M%S")
    name = f"posts-{ts}-{label}.sqlite3"

    # ── get SHA of latest commit ───────────────────────
    r = requests.get(
        f"https://api.github.com/repos/{REPO}/git/refs/heads/{BRANCH}",
        headers={"Authorization": f"Bearer {GITHUB_TOKEN}"})
    r.raise_for_status()
    base_tree = r.json()["object"]["sha"]

    # ── create blob from DB file ───────────────────────
    content = base64.b64encode(DB_PATH.read_bytes()).decode()
    blob = requests.post(
        f"https://api.github.com/repos/{REPO}/git/blobs",
        headers={"Authorization": f"Bearer {GITHUB_TOKEN}"},
        json={"content": content, "encoding": "base64"})
    blob.raise_for_status()
    blob_sha = blob.json()["sha"]

    # ── create tree object with that blob ──────────────
    tree = requests.post(
        f"https://api.github.com/repos/{REPO}/git/trees",
        headers={"Authorization": f"Bearer {GITHUB_TOKEN}"},
        json={"base_tree": base_tree,
              "tree": [{"path": name, "mode": "100644",
                        "type": "blob", "sha": blob_sha}]})
    tree.raise_for_status()
    tree_sha = tree.json()["sha"]

    # ── commit & update ref ────────────────────────────
    commit = requests.post(
        f"https://api.github.com/repos/{REPO}/git/commits",
        headers={"Authorization": f"Bearer {GITHUB_TOKEN}"},
        json={"message": f"DB backup {name}",
              "tree": tree_sha,
              "parents": [base_tree]})
    commit.raise_for_status()
    commit_sha = commit.json()["sha"]

    requests.patch(
        f"https://api.github.com/repos/{REPO}/git/refs/heads/{BRANCH}",
        headers={"Authorization": f"Bearer {GITHUB_TOKEN}"},
        json={"sha": commit_sha})
    print(f"[backup] uploaded → https://github.com/{REPO}/blob/{BRANCH}/{name}")

######################################################################################
# Tracker
######################################################################################

TOKEN                  = os.getenv("DISCORD_TOKEN")  # log in to discorb web ui, developer settings -> network, start typing something and look for typing to show up in the box on the right. Authorization entry in the header is the token.
if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN not set - fly secrets set DISCORD_TOKEN=...")
SOURCE_GUILD_ID        = 226045346399256576     # GemStone IV guild
AGGREGATOR_GUILD_ID    = 1383182313210511472    # GSIV_BlueTracker server
CENTRAL_CHAN_ID        = 1383196587270078515    # #gm-tracker channel
CREATE_COOLDOWN        = 1                     # s to pause after new chan/cat
REPLAY_MODE            = False                 # flip to True for one-time replay
BT_THREADS_PASS        = 0

src_guild: discord.Guild | None = None
dst_guild: discord.Guild | None = None


# ─── roles to track ──────────────────────────────────────────────────────────
TRACKED = {
    587394944897908736,  # Server Admin
    680574750208294924,  # Product Manager
    226053427690471425,  # Senior GameMaster
    226053100790743044,  # GameMaster
}

SEED_BLUE_IDS = {
    308821099863605249,  # Wyrom
    111937766157291520,  # Estild
    316371182146420746,  # Isten
    310436686893023232,  # Thandiwe
    388553211218493451,  # Tivvy
    105139678088278016,  # Auchand
    75093792939581440,   # Mestys
    312977191933575168,  # Vanah
    287728993107443714,  # Elysani
    716406583248289873,  # Xynwen
    205777222102024192,  # Haxus
    436340983718739969,  # Naiken
    287266173673013251,  # Naionna
    287057798955794433,  # Valyrka
    1195153296235712565, # Weaves
    710276421003640862,  # Yusri
    557733619175653386,  # Meraki
    413715970511863808,  # Avaluka
    898650991195463721,  # Casil
    1182779174029635724, # Eusah
    312280391493091332,  # Flannihan
    560411563895422977,  # Itzel
    135457963807735808,  # Scrimge
    321823595107975168,  # Sindin
    562749776026664960,  # Xeraphina                                                                                                                                                                                                      ^P Prev Line
    307156013637828619,  # Elidi                                                                                                                                                                                                          ^N Next Line
    913160493965922345,  # Ethereal                                                                                                                                                                                                               Thu 06-19 23:35
    908492399376998460,  # Marstreforn
    1195134155521020026, # Optheria
    1190437489194844160, # Aergo
    1195603135268405309, # Azidaer
    711671094003630110,  # Gyres
    557733716538163201,  # Irvy
    1181709242487558144, # Kaonashi
    235241271751344128,  # Lydil
    370113695201886210,  # Mariath
    1195186424513839114, # Nyxus
    1083646594823491605, # Tago
    1200407603797303359, # Warlockes
    294990044668624897,  # Zissu
    84034005221019648,   # spiffyjr  (Naijin)
    306987975932248065,  # Retser
    200287510088253440,  # Naos
    307031927192551424,  # Coase
    426755949701890050,  # Quillic
    299691771657715712,  # Xayle
    308625197852917760,  # Ixix
    113793819929083905,  # Konacon
    1195131331047346246, # Apraxis
    190295595125047296,  # Tamuz  (late addition)
    306995432981266433,  # Modrian
}

# ─── channels to ignore from the source guild ───────────────────────────────
IGNORED_CHANNELS = {
    613879283038814228,  # Off-Topic
    1333880748461260921, # Platinum off-topic thread
}

# ─── SQLite setup ───────────────────────────────────────────────────────────
DB_PATH = Path("/data/bluetracker.db")
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

#######################################################################
# LITTLE HELPERS
#######################################################################
import time
_channel_stats = {}   # chan_id → {'empty': int, 'last_pull': float}
mirror_cache  = {}      # (src_guild_id, src_chan_id) ➜ mirror TextChannel
wh_cache      = {}      # mirror_chan_id ➜ webhook
blue_ids      = set()   # GM/CM user-IDs (current + retired)

async def make_read_only(channel: discord.TextChannel):
    default = channel.guild.default_role
    ov      = channel.overwrites_for(default) or discord.PermissionOverwrite()
    if ov.send_messages is False: return
    ov.send_messages = False
    await channel.set_permissions(default, overwrite=ov)

async def ensure_mirror(dst_guild, src_channel: discord.abc.GuildChannel):
    key = (src_channel.guild.id, src_channel.id)
    if key in mirror_cache:
        return mirror_cache[key]

    parent_src = src_channel.parent if isinstance(src_channel, discord.Thread) else src_channel

    cat_name = parent_src.category.name or "No-Category"
    category = discord.utils.get(dst_guild.categories, name=cat_name)
    if category is None:
        category = await dst_guild.create_category(cat_name)
        await asyncio.sleep(CREATE_COOLDOWN)

    mirror_parent = discord.utils.get(category.text_channels, name=parent_src.name)
    if mirror_parent is None:
        mirror_parent = await category.create_text_channel(parent_src.name)
        await make_read_only(mirror_parent)
        await asyncio.sleep(CREATE_COOLDOWN)

    if not isinstance(src_channel, discord.Thread):
        mirror_cache[key] = mirror_parent
        return mirror_parent

    mirror_thread = discord.utils.get(mirror_parent.threads, name=src_channel.name)
    if mirror_thread is None:
        mirror_thread = await mirror_parent.create_thread(
            name = src_channel.name,
            type = discord.ChannelType.public_thread,
            auto_archive_duration = src_channel.auto_archive_duration
        )
        await asyncio.sleep(CREATE_COOLDOWN)

    mirror_cache[key] = mirror_thread
    return mirror_thread

async def get_webhook(channel):
    if channel.id in wh_cache:
        return wh_cache[channel.id]
    hooks = await channel.webhooks()
    for wh in hooks:
        if wh.name == "BlueTracker":
            wh_cache[channel.id] = wh
            return wh
    wh = await channel.create_webhook(name="BlueTracker")
    wh_cache[channel.id] = wh
    return wh

def should_repost(msg):
    roles = getattr(msg.author, "roles", [])
    return (
        msg.channel.id not in IGNORED_CHANNELS and
        (
            TRACKED & {r.id for r in roles} or
            msg.author.id in blue_ids
        )
    )

def thread_arg(ch):
    return ch if isinstance(ch, discord.Thread) else None

async def build_snippet(msg: discord.Message) -> str:
    snippet = msg.content or "(embed/attachment only)"
    if len(snippet) > 200:
        snippet = snippet[:197] + "…"

    if not msg.reference:
        return snippet

    parent = (
        msg.reference.resolved
        if msg.reference and isinstance(msg.reference.resolved, discord.Message)
        else None
    )
    if parent is None:                         # not cached → optional REST fetch
        try:
            parent = await msg.channel.fetch_message(msg.reference.message_id)
            await asyncio.sleep(0.2)           # gentle on rate-limit
        except (discord.NotFound,
                discord.Forbidden,
                discord.HTTPException):
            parent = None

    if parent:
        p_txt = parent.content or "(embed/attachment only)"
        if len(p_txt) > 100:
            p_txt = p_txt[:97] + "…"
        snippet = f"> **↪️   {parent.author.display_name}:** {p_txt}\n\n" + snippet

    return snippet

async def fetchone(conn, query, params=()):
    cur = await conn.execute(query, params)
    row = await cur.fetchone()
    await cur.close()
    return row

async def crawl_one(ch: discord.TextChannel | discord.Thread, cutoff, me):
    global save_counter
    if ch.id in IGNORED_CHANNELS: return
    if not ch.permissions_for(me).read_message_history: return

    row = await fetchone(db, "SELECT MAX(id) FROM posts WHERE chan_id = ?", (ch.id,))
    after_obj = discord.Object(id=int(row[0])) if row and row[0] else None
    pulled = 0

    try:
        async def _page():
            return [m async for m in ch.history(limit=PAGE_SIZE, oldest_first=True, after=after_obj)]
        messages = await asyncio.wait_for(_page(), timeout=15.0)
        for m in messages:
            if m.created_at < cutoff:
                break
            pulled += 1
            if should_repost(m):
                blue_ids.add(m.author.id)
                await db_add_author(m.author)
                snippet = await build_snippet(m)     # your helper
                rc = await db.execute(
                    "INSERT OR IGNORE INTO posts VALUES (?,?,?,?,?, 0)",
                    (m.id, ch.id, m.author.id,
                    int(m.created_at.timestamp()*1000),
                    snippet)
                    )
                if rc.rowcount:                     # only if new row
                    save_counter += 1
                    if save_counter % CRAWL_VERBOSITY == 0:
                        print(f"[crawler] {save_counter:,} saved  –  "
                              f"{ch.guild.name} ▸ #{ch.name}  @  "
                              f"{m.created_at:%Y-%m-%d %H:%M:%S}")
                    await db.commit()
    except asyncio.TimeoutError:
        print(f"[crawler] TIMEOUT in #{ch.name} - skipping this pass")
    except discord.Forbidden:
        return
    except Exception as e:
        print(f"[crawler] Error in #{ch.name}: {e}")

    _dbg(ch, pulled, save_counter)

async def iter_all_threads(parent: discord.TextChannel):
    # active first
    for th in parent.threads:
        yield th

    # archived PUBLIC threads only
    try:
        archived = [
            th async for th in parent.archived_threads(
                limit=None,  # keep newest-first
                private=False   # <-- public only
            )
        ]
        for th in reversed(archived):           # oldest-first
            yield th
    except discord.Forbidden:
        print(f"[crawler] No access to archived threads in #{parent.name}. Skipping")
        return
    except discord.HTTPException as e:
        if 500 <= e.status < 600 or e.status == 429:
            print(f"[crawler] Skipping archived threads in #{parent.name}: "
                  f"{e.status} {e.text or ''}".strip())
            return
        raise

def _dbg(ch, pulled, saved):
    """One-line heartbeat for each crawl_one run."""
    now   = time.time()
    delta = now - _channel_stats.get(ch.id, {}).get('last_pull', now)
    print(f"[crawler] #{ch.name:<28} pulled={pulled:<3}  "
          f"total_saved={saved:<6}  idle={delta:>5.1f}s")
    _channel_stats[ch.id] = {'last_pull': now,
                             'empty': _channel_stats.get(ch.id, {}).get('empty', 0)
                                      + (pulled == 0)}


#######################################################################
# DISCORD CLIENT
#######################################################################
client = discord.Client()

#######################################################################
# DATABASE CONNECTION  (opened in on_ready)
#######################################################################
db = None  # type: aiosqlite.Connection

async def db_add_post(m: discord.Message, prepared_text: str):
    await db.execute(
        "INSERT OR IGNORE INTO posts VALUES (?,?,?,?,?, 0)",
        (m.id, m.channel.id, m.author.id,
         int(m.created_at.timestamp()*1000), prepared_text)
    )

async def db_add_author(user: discord.User | discord.Member):
    await db.execute(
        "INSERT OR IGNORE INTO authors VALUES (?, ?)",
        (user.id, user.display_name or user.name)   # fall back to .name
    )
    await db.execute(
        "UPDATE authors SET author_name = ? "
        "WHERE author_id = ? AND author_name IS NULL",
        (user.display_name or user.name, user.id)
    )

async def db_commit():
    await db.commit()

#######################################################################
# LIVE REPOST LOGIC  (central feed + mirrored channel)
#######################################################################
async def repost_live(m: discord.Message, dst_guild):
    jump    = f"https://discord.com/channels/{m.guild.id}/{m.channel.id}/{m.id}"
    snippet = await build_snippet(m)

    content = (
        f"{m.author.display_name} ({m.guild.name} • #{m.channel.name}):\n"
        f"{snippet}\n{jump}"
    )

    # central feed
    central = client.get_channel(CENTRAL_CHAN_ID)
    if central:
        wh = await get_webhook(central)
        await wh.send(content      = content,
                      username     = m.author.display_name,
                      avatar_url   = m.author.display_avatar.url,
                      allowed_mentions = discord.AllowedMentions.none())

    # mirrored hierarchy
    mirror = await ensure_mirror(dst_guild, m.channel)
    is_thread = isinstance(mirror, discord.Thread)
    parent = mirror.parent if is_thread else mirror
    wh2    = await get_webhook(parent)

    kwargs = dict(
        content      = content,
        username     = m.author.display_name,
        avatar_url   = m.author.display_avatar.url,
        allowed_mentions = discord.AllowedMentions.none(),
    )
    if is_thread:
        kwargs["thread"] = mirror
    await wh2.send(**kwargs)

#######################################################################
# SLOW-DRIP CRAWLER   (runs permanently until caught up)
#######################################################################
REQ_PAUSE = 2.5          # seconds between history requests
PAGE_SIZE = 50
CUTOFF_DAYS = 365*10     # how far back to go
CRAWL_VERBOSITY = 10
save_counter = 0

async def slow_crawl(src_guild: discord.Guild):
    me      = src_guild.get_member(client.user.id) \
              or await src_guild.fetch_member(client.user.id)
    cutoff  = datetime.utcnow().replace(tzinfo=timezone.utc) \
              - timedelta(days=CUTOFF_DAYS)

    while True:
        for parent in src_guild.text_channels:
            # crawl the parent channel itself
            await crawl_one(parent, cutoff, me)
            await asyncio.sleep(REQ_PAUSE)

            # crawl every active + archived thread under it
            async for th in iter_all_threads(parent):
                await crawl_one(th, cutoff, me)
                await asyncio.sleep(REQ_PAUSE)

        await asyncio.sleep(30)      # half-minute break between full sweeps

async def crawl_threads_only(src_guild):
    me = src_guild.get_member(client.user.id) \
         or await src_guild.fetch_member(client.user.id)

    cutoff = datetime.utcnow().replace(tzinfo=timezone.utc) \
             - timedelta(days=CUTOFF_DAYS)

    print("► Starting thread-only crawl …")
    global save_counter
    for parent in src_guild.text_channels:
        if parent.id in IGNORED_CHANNELS:
            continue
        if not parent.permissions_for(me).read_message_history:
            continue

        async for th in iter_all_threads(parent):
            await crawl_one(th, cutoff, me)
            await asyncio.sleep(REQ_PAUSE)      # polite pause between threads

    await db_commit()
    print(f"► Thread-only crawl complete; total rows: {save_counter:,}")

#######################################################################
# ONE-TIME REPLAY (set REPLAY_MODE = True, restart add-on)
#######################################################################
API_PAUSE = 2.1       # per-message pause to stay under webhook rate-limit

async def replay_all(dst_guild):
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
            src_ch  = client.get_channel(int(chan_id))
            if src_ch is None:                            # cache miss – fetch once
                try:
                    src_ch = await client.fetch_channel(int(chan_id))
                except discord.NotFound:
                    continue                # channel deleted; skip this message

            src_guild_name  = src_ch.guild.name
            src_channel_name = src_ch.name

            row = await fetchone(
                db,
                "SELECT author_name FROM authors WHERE author_id = ?",
                (author_id,),
            )
            if row and row[0]:
                display_name = row[0]
                avatar = None               # avatar is a nice-to-have; skip API call
            else:
                user = client.get_user(author_id) or await client.fetch_user(author_id)
                display_name = user.display_name if user else f"ID {author_id}"
                avatar = user.display_avatar.url if user else None

            jump    = f"https://discord.com/channels/{src_ch.guild.id}/{chan_id}/{msg_id}"
            snippet = body or "(embed/attachment only)"
            if len(snippet) > 200:
                snippet = snippet[:197] + "…"

            full = (
                f"{display_name} ({src_guild_name} • #{src_channel_name}):\n"
                f"{snippet}\n{jump}"
            )

            mirror = await ensure_mirror(dst_guild, src_ch)
            is_thread = isinstance(mirror, discord.Thread)
            parent = mirror.parent if is_thread else mirror
            wh     = await get_webhook(parent)
            kwargs = dict(
                content            = full,
                username           = display_name,
                avatar_url         = avatar,
                allowed_mentions   = discord.AllowedMentions.none(),
            )
            if is_thread:
              kwargs["thread"] = mirror
            await wh.send(**kwargs)
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

    print("► Replay complete")

#######################################################################
# EVENT HANDLERS
#######################################################################
@client.event
async def on_ready():
    global db, src_guild, dst_guild
    db = await aiosqlite.connect(DB_PATH)
    await db.executescript(CREATE_SQL)

    try:
        await db.execute("ALTER TABLE posts ADD COLUMN replayed INTEGER DEFAULT 0")
        print("[DB] Added 'replayed' column to posts table.")
    except aiosqlite.OperationalError as e:
        if "duplicate column name" in str(e).lower():
            print("[DB] 'replayed' column already exists. Skipping.")
        else:
            raise

    blue_ids.update(SEED_BLUE_IDS)
    for uid in SEED_BLUE_IDS:
        await db.execute("INSERT OR IGNORE INTO authors VALUES (?, ?)", (uid, None))
    await db.commit()
    print(f"[Self-Bot] Logged in as {client.user} ({client.user.id})")

    src_guild = client.get_guild(SOURCE_GUILD_ID)
    dst_guild = client.get_guild(AGGREGATOR_GUILD_ID)
    while not src_guild or not dst_guild:
        await asyncio.sleep(1)
        src_guild = client.get_guild(SOURCE_GUILD_ID)
        dst_guild = client.get_guild(AGGREGATOR_GUILD_ID)

    row = await fetchone(db, "SELECT COUNT(*) FROM posts")
    total = row[0] if row else 0
    print(f"[DB] posts table currently holds {total:,} rows.")

    asyncio.create_task(asyncio.to_thread(github_backup, "startup"))

    if not REPLAY_MODE:
        # start the gentle crawler
        asyncio.create_task(slow_crawl(src_guild))
        if BT_THREADS_PASS == 1:
            asyncio.create_task(crawl_threads_only(src_guild))
    else:
        # wipe mirror channels manually first, then restart add-on in replay mode
        asyncio.create_task(replay_all(dst_guild))

@client.event
async def on_message(m: discord.Message):
    if m.author.bot or m.author.id == client.user.id:
        return
    if m.guild and m.guild.id != SOURCE_GUILD_ID:
        return
    if not should_repost(m):
        return

    dst_guild = client.get_guild(AGGREGATOR_GUILD_ID)
    await repost_live(m, dst_guild)

    # store live post → DB
    blue_ids.add(m.author.id)
    await db_add_author(m.author)
    snippet = await build_snippet(m)
    await db_add_post(m, snippet)
    await db_commit()

###############################################################################
client.run(TOKEN)    # self-bot login  (no bot=False needed with discord.py-self)
###############################################################################    
