import discord, asyncio
from .config import CENTRAL_CHAN_ID, CREATE_COOLDOWN
from .config import TRACKED_ROLE_IDS, IGNORED_CHANNELS
mirror_cache, wh_cache = {}, {}

def should_repost(msg, blue_ids):
    if msg.channel.id in IGNORED_CHANNELS: return False
    if msg.author.id in blue_ids:          return True
    role_ids = {r.id for r in getattr(msg.author, "roles", [])}
    return bool(role_ids & TRACKED_ROLE_IDS)

async def ensure_mirror(dst_guild, src_channel):
    key = (src_channel.guild.id, src_channel.id)
    if key in mirror_cache: return mirror_cache[key]

    parent_src = src_channel.parent if isinstance(src_channel, discord.Thread) else src_channel
    cat_name   = parent_src.category.name or "No-Category"

    category = discord.utils.get(dst_guild.categories, name=cat_name) \
               or await dst_guild.create_category(cat_name)
    await asyncio.sleep(CREATE_COOLDOWN)

    mirror_parent = discord.utils.get(category.text_channels, name=parent_src.name) \
                    or await category.create_text_channel(parent_src.name)
    await asyncio.sleep(CREATE_COOLDOWN)

    if not isinstance(src_channel, discord.Thread):
        mirror_cache[key] = mirror_parent
        return mirror_parent

    mirror_thread = discord.utils.get(mirror_parent.threads, name=src_channel.name) \
                    or await mirror_parent.create_thread(
                        name=src_channel.name,
                        type=discord.ChannelType.public_thread,
                        auto_archive_duration=src_channel.auto_archive_duration)
    await asyncio.sleep(CREATE_COOLDOWN)

    mirror_cache[key] = mirror_thread
    return mirror_thread

async def get_webhook(channel):
    if channel.id in wh_cache: return wh_cache[channel.id]
    hooks = await channel.webhooks()
    for h in hooks:
        if h.name == "BlueTracker":
            wh_cache[channel.id] = h
            return h
    wh = await channel.create_webhook(name="BlueTracker")
    wh_cache[channel.id] = wh
    return wh

async def build_snippet(msg: discord.Message) -> str:
    txt = msg.content or "(embed/attachment only)"
    if len(txt) > 200:
        txt = txt[:197] + "…"
    return txt

async def repost_live(msg: discord.Message, dst_guild):
    """Send one GM/CM message to central channel and mirrored hierarchy."""
    jump = f"https://discord.com/channels/{msg.guild.id}/{msg.channel.id}/{msg.id}"
    snippet = await build_snippet(msg)

    body = f"{msg.author.display_name} ({msg.guild.name} • #{msg.channel.name}):\n" \
           f"{snippet}\n{jump}"

    # central feed
    central = msg._state._get_client().get_channel(CENTRAL_CHAN_ID)
    if central:
        wh = await get_webhook(central)
        await wh.send(body,
                      username=msg.author.display_name,
                      avatar_url=msg.author.display_avatar.url,
                      allowed_mentions=discord.AllowedMentions.none())

    # mirrored hierarchy
    mirror = await ensure_mirror(dst_guild, msg.channel)
    is_thread = isinstance(mirror, discord.Thread)
    parent    = mirror.parent if is_thread else mirror
    wh2       = await get_webhook(parent)

    kwargs = dict(content=body,
                  username=msg.author.display_name,
                  avatar_url=msg.author.display_avatar.url,
                  allowed_mentions=discord.AllowedMentions.none())
    if is_thread:
        kwargs["thread"] = mirror

    await wh2.send(**kwargs)