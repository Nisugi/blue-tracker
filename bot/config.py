import os
from pathlib import Path

# Set to true to repost new messages from the database
REPLAY_MODE = False
FULL_BACKFILL_RUN = True

TOKEN         = os.getenv("DISCORD_TOKEN")
GITHUB_TOKEN  = os.getenv("GITHUB_TOKEN")
if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN not set (fly secrets set …)")

SOURCE_GUILD_ID     = 226045346399256576   # GemStone IV
AGGREGATOR_GUILD_ID = 1383182313210511472  # BlueTracker
CENTRAL_CHAN_ID     = 1383196587270078515  # #gm-tracker
CREATE_COOLDOWN     = 1

DB_PATH = Path("/data/bluetracker.db")

# Crawler settings
REQ_PAUSE = 2.5          # seconds between history requests
PAGE_SIZE = 50
CUTOFF_DAYS = 365 * 10   # how far back to go
CRAWL_VERBOSITY = 10     # print progress every N saves

# Replay settings
API_PAUSE = 2.1          # per-message pause for webhook rate-limit

# Roles to track for live reposting
TRACKED = {
    587394944897908736,  # Server Admin
    680574750208294924,  # Product Manager
    226053427690471425,  # Senior GameMaster
    226053100790743044,  # GameMaster
}

# UserIDs to track archived posts or retired gms that no longer have roles
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
    562749776026664960,  # Xeraphina
    307156013637828619,  # Elidi
    913160493965922345,  # Ethereal
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

# channels to ignore from the source guild
IGNORED_CHANNELS = {
    613879283038814228,  # Off-Topic
    1333880748461260921, # Platinum off-topic thread
    1171221232402845767, # Games and Trivia
}

GM_NAME_OVERRIDES = {
    84034005221019648: "Naijin",           # spiffyjr
    111937766157291520: "Estild",          # glyph.dev  
    308821099863605249: "Wyrom",           # Keep as Wyrom
    316371182146420746: "Isten",           # 
    310436686893023232: "Thandiwe",        #
    388553211218493451: "Tivvy",           #
    105139678088278016: "Auchand",         #
    75093792939581440: "Mestys",           #
    312977191933575168: "Vanah",           #
    287728993107443714: "Elysani",         #
    716406583248289873: "Xynwen",          #
    205777222102024192: "Haxus",           #
    436340983718739969: "Naiken",          #
    287266173673013251: "Naionna",         #
    287057798955794433: "Valyrka",         #
    1195153296235712565: "Weaves",         #
    710276421003640862: "Yusri",           #
    557733619175653386: "Meraki",          #
    413715970511863808: "Avaluka",         #
    898650991195463721: "Casil",           #
    1182779174029635724: "Eusah",          #
    312280391493091332: "Flannihan",       #
    560411563895422977: "Itzel",           #
    135457963807735808: "Scrimge",         #
    321823595107975168: "Sindin",          #
    562749776026664960: "Xeraphina",       #
    307156013637828619: "Elidi",           #
    913160493965922345: "Ethereal",        #
    908492399376998460: "Marstreforn",     #
    1195134155521020026: "Optheria",       #
    1190437489194844160: "Aergo",          #
    1195603135268405309: "Azidaer",        #
    711671094003630110: "Gyres",           #
    557733716538163201: "Irvy",            #
    1181709242487558144: "Kaonashi",       #
    235241271751344128: "Lydil",           #
    370113695201886210: "Mariath",         #
    1195186424513839114: "Nyxus",          #
    1083646594823491605: "Tago",           #
    1200407603797303359: "Warlockes",      #
    294990044668624897: "Zissu",           #
    306987975932248065: "Retser",          #
    200287510088253440: "Naos",            #
    307031927192551424: "Coase",           #
    426755949701890050: "Quillic",         #
    299691771657715712: "Xayle",           #
    308625197852917760: "Ixix",            #
    113793819929083905: "Konacon",         #
    1195131331047346246: "Apraxis",        #
    190295595125047296: "Tamuz",           #
    306995432981266433: "Modrian",         #
}
