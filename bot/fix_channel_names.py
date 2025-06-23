#!/usr/bin/env python3
"""
Fix channel names in the BlueTracker database.
This script will:
1. Clean up numeric/placeholder channel names
2. Fetch proper names from Discord for channels/threads
3. Update the database with correct names
"""

import asyncio
import discord
import aiosqlite
import re
from pathlib import Path
from datetime import datetime

# Configuration
TOKEN = None  # Will be set from environment
SOURCE_GUILD_ID = 226045346399256576
DB_PATH = Path("/data/bluetracker.db")
IGNORED_CHANNELS = {
    613879283038814228,  # Off-Topic
    1333880748461260921, # Platinum off-topic thread
}

# Regex to match problematic channel names
NUMERIC_PATTERN = re.compile(r'^#?\d+$')

async def get_channel_name(client, channel_id):
    """Fetch the actual name of a channel/thread from Discord"""
    try:
        channel = client.get_channel(int(channel_id))
        if not channel:
            channel = await client.fetch_channel(int(channel_id))
        return channel.name if channel else None
    except discord.NotFound:
        print(f"[fix] Channel {channel_id} not found (deleted)")
        return None
    except discord.Forbidden:
        print(f"[fix] No access to channel {channel_id}")
        return None
    except Exception as e:
        print(f"[fix] Error fetching channel {channel_id}: {e}")
        return None

async def fix_channel_names(db, client):
    """Fix all channel names in the database"""
    print("[fix] Starting channel name fixes...")
    
    # Step 1: Find all channels with problematic names
    cursor = await db.execute("""
        SELECT DISTINCT chan_id, name
        FROM channels
        WHERE name IS NULL 
           OR name = ''
           OR name GLOB '[0-9]*'
           OR name GLOB '#[0-9]*'
        ORDER BY chan_id
    """)
    
    problematic_channels = await cursor.fetchall()
    print(f"[fix] Found {len(problematic_channels)} channels with problematic names")
    
    # Step 2: Also find channels referenced in posts but not in channels table
    cursor = await db.execute("""
        SELECT DISTINCT p.chan_id
        FROM posts p
        LEFT JOIN channels c ON p.chan_id = c.chan_id
        WHERE c.chan_id IS NULL
           AND p.chan_id IS NOT NULL
           AND p.chan_id != ''
    """)
    
    missing_channels = await cursor.fetchall()
    print(f"[fix] Found {len(missing_channels)} channels in posts but not in channels table")
    
    # Step 3: Process all problematic channels
    fixed_count = 0
    failed_count = 0
    
    for row in problematic_channels:
        chan_id, current_name = row
        
        # Skip if it's an ignored channel
        try:
            if int(chan_id) in IGNORED_CHANNELS:
                continue
        except ValueError:
            pass
        
        print(f"[fix] Processing channel {chan_id} (current: '{current_name}')")
        
        # Fetch the real name from Discord
        real_name = await get_channel_name(client, chan_id)
        
        if real_name:
            # Update the database
            await db.execute("""
                UPDATE channels 
                SET name = ? 
                WHERE chan_id = ?
            """, (real_name, chan_id))
            print(f"[fix] ✓ Updated {chan_id} -> {real_name}")
            fixed_count += 1
        else:
            print(f"[fix] ✗ Could not fetch name for {chan_id}")
            failed_count += 1
        
        # Be nice to Discord API
        await asyncio.sleep(0.5)
    
    # Step 4: Add missing channels to channels table
    for row in missing_channels:
        chan_id = row[0]
        
        print(f"[fix] Adding missing channel {chan_id} to channels table")
        
        # Try to get channel info
        real_name = await get_channel_name(client, chan_id)
        
        if real_name:
            # Determine if it's a thread by trying to get parent info
            parent_id = None
            try:
                channel = client.get_channel(int(chan_id)) or await client.fetch_channel(int(chan_id))
                if isinstance(channel, discord.Thread):
                    parent_id = str(channel.parent_id)
            except:
                pass
            
            await db.execute("""
                INSERT OR REPLACE INTO channels (chan_id, name, parent_id, accessible)
                VALUES (?, ?, ?, 1)
            """, (chan_id, real_name, parent_id))
            print(f"[fix] ✓ Added {chan_id} -> {real_name}")
            fixed_count += 1
        else:
            # Add with placeholder but mark as inaccessible
            await db.execute("""
                INSERT OR REPLACE INTO channels (chan_id, name, accessible)
                VALUES (?, ?, 0)
            """, (chan_id, f"deleted-{chan_id}"))
            failed_count += 1
        
        await asyncio.sleep(0.5)
    
    # Step 5: Clean up posts table - remove numeric chan_id values
    cursor = await db.execute("""
        SELECT COUNT(*) FROM posts 
        WHERE chan_id GLOB '[0-9]*'
    """)
    numeric_count = (await cursor.fetchone())[0]
    
    if numeric_count > 0:
        print(f"[fix] Found {numeric_count} posts with numeric chan_id values")
        # These are likely old artifacts - update them to NULL
        await db.execute("""
            UPDATE posts 
            SET chan_id = NULL 
            WHERE chan_id GLOB '[0-9]*'
        """)
    
    # Commit all changes
    await db.commit()
    
    print(f"\n[fix] Summary:")
    print(f"[fix] ✓ Fixed: {fixed_count} channels")
    print(f"[fix] ✗ Failed: {failed_count} channels")
    print(f"[fix] Cleaned: {numeric_count} numeric chan_id values in posts")
    
    return fixed_count, failed_count

async def verify_fixes(db):
    """Verify the fixes were applied correctly"""
    print("\n[fix] Verifying fixes...")
    
    # Check for remaining problematic names
    cursor = await db.execute("""
        SELECT COUNT(*) FROM channels
        WHERE name IS NULL 
           OR name = ''
           OR name GLOB '[0-9]*'
           OR name GLOB '#[0-9]*'
    """)
    remaining = (await cursor.fetchone())[0]
    
    # Check for posts without channel info
    cursor = await db.execute("""
        SELECT COUNT(*) FROM posts p
        LEFT JOIN channels c ON p.chan_id = c.chan_id
        WHERE p.chan_id IS NOT NULL 
          AND p.chan_id != ''
          AND c.chan_id IS NULL
    """)
    orphaned = (await cursor.fetchone())[0]
    
    print(f"[fix] Remaining problematic channel names: {remaining}")
    print(f"[fix] Posts with missing channel info: {orphaned}")
    
    # Show sample of properly named channels
    cursor = await db.execute("""
        SELECT chan_id, name FROM channels 
        WHERE name NOT GLOB '[0-9]*' 
          AND name NOT GLOB '#[0-9]*'
          AND name IS NOT NULL
          AND name != ''
        LIMIT 10
    """)
    
    print("\n[fix] Sample of properly named channels:")
    async for row in cursor:
        print(f"[fix]   {row[0]} -> {row[1]}")

async def main():
    """Main function to run the fix"""
    import os
    
    # Get token from environment
    global TOKEN
    TOKEN = os.getenv("DISCORD_TOKEN")
    if not TOKEN:
        print("[fix] Error: DISCORD_TOKEN not set")
        return
    
    # Ensure database exists
    if not DB_PATH.exists():
        print(f"[fix] Error: Database not found at {DB_PATH}")
        return
    
    print(f"[fix] Opening database at {DB_PATH}")
    
    # Create Discord client
    intents = discord.Intents.default()
    intents.message_content = True
    intents.guilds = True
    client = discord.Client(intents=intents)
    
    @client.event
    async def on_ready():
        print(f"[fix] Connected as {client.user}")
        
        # Wait for guild to be available
        guild = client.get_guild(SOURCE_GUILD_ID)
        if not guild:
            print(f"[fix] Waiting for guild {SOURCE_GUILD_ID}...")
            await asyncio.sleep(2)
            guild = client.get_guild(SOURCE_GUILD_ID)
        
        if not guild:
            print(f"[fix] Error: Could not access guild {SOURCE_GUILD_ID}")
            await client.close()
            return
        
        print(f"[fix] Found guild: {guild.name}")
        
        # Open database
        async with aiosqlite.connect(DB_PATH) as db:
            # Enable foreign keys
            await db.execute("PRAGMA foreign_keys = ON")
            
            # Run the fixes
            await fix_channel_names(db, client)
            
            # Verify the fixes
            await verify_fixes(db)
        
        print("\n[fix] Done! Closing connection...")
        await client.close()
    
    # Run the client
    try:
        await client.start(TOKEN)
    except KeyboardInterrupt:
        print("\n[fix] Interrupted by user")
    finally:
        if not client.is_closed():
            await client.close()

if __name__ == "__main__":
    print("[fix] BlueTracker Channel Name Fixer")
    print("[fix] This will fix numeric/missing channel names in the database")
    print("[fix] Starting in 3 seconds... (Ctrl+C to cancel)")
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[fix] Cancelled by user")
