# GSIV Blue-Tracker Setup Guide

A Discord application that tracks and mirrors GemStone IV staff messages from the official server to a dedicated tracking server. This application crawls historical messages and relays new ones in real-time.

## 🎯 What This Application Does

- **Live Relay**: Instantly reposts GM/CM messages to a central tracking channel
- **Mirror Hierarchy**: Recreates the source server's channel structure for organized browsing
- **Historical Crawling**: Slowly crawls years of message history without hitting rate limits
- **GitHub Backups**: Automatically backs up the database for safekeeping
- **Replay Mode**: Can replay all stored messages to rebuild mirror channels

## 🏗️ Hosting Platform

This guide uses **Fly.io** for hosting, but the application can run on any platform that supports Python and persistent storage. We chose Fly.io because:

- Simple deployment with Docker
- Reliable persistent volumes
- Good free tier (3 shared VMs included)
- Easy secret management

**Alternative hosting options** (not covered in this guide):
- Railway, Render, DigitalOcean App Platform
- Self-hosted VPS (Linux server)
- Local machine (less reliable)

**Note**: We couldn't find a completely free hosting solution that met our needs (persistent storage + 24/7 uptime), but Fly.io costs only ~$5-10/month.

## 📋 Prerequisites

Before starting, you'll need:

1. **Discord Account**: Your personal Discord account that can see the GemStone IV server
2. **GitHub Account**: For database backups (optional but recommended)
3. **Fly.io Account**: For hosting the application (or alternative hosting platform)
4. **Credit Card**: Fly.io requires payment info (free tier available)

## 🚀 Step 1: Create Fly.io Account

1. Go to [fly.io](https://fly.io)
2. Click **"Sign Up"** 
3. Create account with GitHub or email
4. **Add payment method** (required even for free tier)
5. Install the Fly CLI:
   ```bash
   # macOS
   brew install flyctl
   
   # Windows (PowerShell)
   iwr https://fly.io/install.ps1 -useb | iex
   
   # Linux
   curl -L https://fly.io/install.sh | sh
   ```
6. Login to Fly CLI:
   ```bash
   flyctl auth login
   ```

## 🔧 Step 2: Clone and Setup Repository

1. **Clone the repository:**
   ```bash
   git clone https://github.com/Nisugi/GSIV-BlueTracker.git
   cd GSIV-BlueTracker
   ```

2. **Review the configuration:**
   Open `bot/config.py` and verify:
   - `SOURCE_GUILD_ID`: Should be `226045346399256576` (GemStone IV server)
   - `AGGREGATOR_GUILD_ID`: Your tracking server ID
   - `CENTRAL_CHAN_ID`: Your central tracking channel ID
   - `REPLAY_MODE`: Set to `False` for normal operation

## 🔑 Step 3: Get Your Discord Token

**⚠️ WARNING: User applications violate Discord's Terms of Service. Use at your own risk.**

1. Open Discord in your web browser (not the app)
2. Login to your account
3. Open Developer Tools (F12)
4. Go to **Network** tab
5. Type something in any channel
6. Look for a request called **"typing"**
7. In the request headers, find **"Authorization"**
8. Copy the value after "Authorization: " (this is your token)

**Keep this token secret! Don't share it with anyone.**

## 🏗️ Step 4: Create Your Tracking Server

If you don't already have a Discord server for tracking:

1. Create a new Discord server
2. Create a channel called `#gm-tracker`
3. Note the server ID and channel ID:
   - Enable Developer Mode in Discord settings
   - Right-click server name → Copy Server ID
   - Right-click channel → Copy Channel ID
4. Update `bot/config.py` with these IDs

## 🌐 Step 5: Deploy to Fly.io

1. **Create the Fly app:**
   ```bash
   flyctl apps create blue-tracker
   ```

2. **Create a volume for database storage:**
   ```bash
   flyctl volumes create db_volume --region dfw --size 1
   ```

3. **Set your Discord token as a secret:**
   ```bash
   flyctl secrets set DISCORD_TOKEN="your_discord_token_here"
   ```

4. **Set GitHub token for backups (optional but recommended):**
   ```bash
   flyctl secrets set GITHUB_TOKEN="your_github_token_here"
   ```

5. **Deploy the bot:**
   ```bash
   flyctl deploy
   ```

## 🔄 Step 6: GitHub Backup Setup (Optional)

To enable automatic database backups:

1. **Create a GitHub repository** for storing backups
2. **Create a fine-grained personal access token:**
   - Go to GitHub Settings → Developer settings → Personal access tokens → Fine-grained tokens
   - Click "Generate new token"
   - Select your backup repository
   - Grant **Contents: Read and write** permissions
   - Copy the token

3. **Update the repository name in code:**
   Edit `bot/github_backup.py`:
   ```python
   REPO = "YourUsername/YourBackupRepo"  # Change this line
   ```

4. **Set the token:**
   ```bash
   flyctl secrets set GITHUB_TOKEN="your_github_token_here"
   ```

## 🔍 Step 7: Monitor and Verify

1. **Check logs to see if it's working:**
   ```bash
   flyctl logs
   ```

2. **Look for these startup messages:**
   ```
   [Application] Logged in as YourUsername#1234 (123456789)
   [DB] posts table currently holds 0 rows.
   [crawler] Starting slow crawl with 3650 day cutoff
   ```

3. **Send a test message** in the GemStone IV server if you have GM/CM role, or wait for a real GM message

## ⚙️ Step 8: Configuration Options

### Normal Operation Mode
- Set `REPLAY_MODE = False` in `bot/config.py`
- Application will crawl historical messages slowly and relay new ones live

### Replay Mode (for rebuilding channels)
- Set `REPLAY_MODE = True` in `bot/config.py`
- Deploy the update: `flyctl deploy`
- Application will replay all stored messages, then switch to normal crawling
- **Set back to `False`** after replay completes

### Adding New Staff Members
Edit `SEED_BLUE_IDS` in `bot/config.py` to add new GM/CM user IDs:
```python
SEED_BLUE_IDS = {
    308821099863605249,  # Wyrom
    # Add new IDs here
    123456789012345678,  # New GM Name
}
```

## 📊 Management Commands

### View logs:
```bash
flyctl logs
```

### View app status:
```bash
flyctl status
```

### Restart the application:
```bash
flyctl apps restart blue-tracker
```

### Scale resources (if needed):
```bash
flyctl scale memory 512  # Reduce to 512MB
flyctl scale memory 1024 # Increase to 1GB
```

### Access database (advanced):
```bash
flyctl ssh console
# Inside the container:
sqlite3 /data/bluetracker.db
.tables
.quit
exit
```

## 🛠️ Troubleshooting

### Application won't start:
- Check logs: `flyctl logs`
- Verify Discord token is correct
- Ensure account can see the source server

### No messages being tracked:
- Verify `SOURCE_GUILD_ID` matches GemStone IV server
- Check that tracked users have the right roles
- Look for permission errors in logs

### Mirror channels not created:
- Verify `AGGREGATOR_GUILD_ID` is correct
- Ensure account is in the tracking server
- Check for webhook creation errors in logs

### Database issues:
- Volume might be full: `flyctl volumes list`
- Check volume mount in `fly.toml`

### Rate limiting:
- Application automatically handles Discord rate limits
- GitHub backup failures are logged but won't stop the application

## 💰 Cost Estimates

**Fly.io costs (as of 2024):**
- **Shared CPU, 1GB RAM**: ~$5-10/month
- **1GB Volume**: ~$0.15/month
- **Total**: ~$5-11/month

**Free tier includes:**
- 3 shared-cpu-1x VMs
- 160GB outbound data transfer

## 🔒 Security Notes

- **Never share your Discord token**
- **User applications violate Discord TOS** - use at your own risk
- **GitHub tokens** should have minimal permissions (Contents: read/write only)
- **Fly secrets** are encrypted and not visible in logs

## 📝 Maintenance

### Regular tasks:
- **Monitor logs** for errors: `flyctl logs`
- **Check database size**: Growth rate depends on message volume
- **Update staff IDs** when GMs join/leave

### Updates:
1. Pull latest code: `git pull`
2. Deploy: `flyctl deploy`
3. Monitor logs for issues

## 🆘 Support

If you run into issues:

1. **Check the logs first**: `flyctl logs`
2. **Review configuration**: Verify all IDs and tokens
3. **Test Discord access**: Ensure your account can see both servers
4. **GitHub Issues**: Report bugs with relevant log snippets

## ⚖️ Legal Disclaimer

This application uses Discord's undocumented user authentication functionality, which violates Discord's Terms of Service. Use at your own risk. The authors are not responsible for any account suspensions or other consequences.
