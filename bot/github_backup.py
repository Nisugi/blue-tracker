import base64, time, requests
from .config import GITHUB_TOKEN, DB_PATH

REPO   = "Nisugi/GSIV-BlueTracker"
BRANCH = "main"

def github_backup(label="auto"):
    """Upload database backup to GitHub repository"""
    if not GITHUB_TOKEN:
        print("[backup] GITHUB_TOKEN not set – skipping")
        return

    if not DB_PATH.exists():
        print("[backup] Database file doesn't exist – skipping")
        return

    try:
        ts = time.strftime("%Y%m%d-%H%M%S")
        name = f"posts-{ts}-{label}.sqlite3"
        headers = {"Authorization": f"Bearer {GITHUB_TOKEN}"}

        # Get SHA of latest commit
        print(f"[backup] Starting backup: {name}")
        base_response = requests.get(
            f"https://api.github.com/repos/{REPO}/git/refs/heads/{BRANCH}",
            headers=headers,
            timeout=30
        )
        base_response.raise_for_status()
        base_sha = base_response.json()["object"]["sha"]

        # Create blob from DB file
        print("[backup] Creating blob...")
        blob_response = requests.post(
            f"https://api.github.com/repos/{REPO}/git/blobs",
            headers=headers,
            json={
                "content": base64.b64encode(DB_PATH.read_bytes()).decode(),
                "encoding": "base64"
            },
            timeout=60
        )
        blob_response.raise_for_status()
        blob_sha = blob_response.json()["sha"]

        # Create tree object
        print("[backup] Creating tree...")
        tree_response = requests.post(
            f"https://api.github.com/repos/{REPO}/git/trees",
            headers=headers,
            json={
                "base_tree": base_sha,
                "tree": [{
                    "path": name,
                    "mode": "100644",
                    "type": "blob",
                    "sha": blob_sha
                }]
            },
            timeout=30
        )
        tree_response.raise_for_status()
        tree_sha = tree_response.json()["sha"]

        # Create commit
        print("[backup] Creating commit...")
        commit_response = requests.post(
            f"https://api.github.com/repos/{REPO}/git/commits",
            headers=headers,
            json={
                "message": f"DB backup {name}",
                "tree": tree_sha,
                "parents": [base_sha]
            },
            timeout=30
        )
        commit_response.raise_for_status()
        commit_sha = commit_response.json()["sha"]

        # Update branch reference
        print("[backup] Updating branch...")
        ref_response = requests.patch(
            f"https://api.github.com/repos/{REPO}/git/refs/heads/{BRANCH}",
            headers=headers,
            json={"sha": commit_sha},
            timeout=30
        )
        ref_response.raise_for_status()

        print(f"[backup] ✓ Uploaded → https://github.com/{REPO}/blob/{BRANCH}/{name}")
        
    except requests.exceptions.Timeout:
        print("[backup] ✗ Failed: Request timed out")
    except requests.exceptions.ConnectionError:
        print("[backup] ✗ Failed: Connection error")
    except requests.exceptions.HTTPError as e:
        print(f"[backup] ✗ Failed: HTTP {e.response.status_code} - {e.response.text}")
    except Exception as e:
        print(f"[backup] ✗ Failed: {e}")

async def safe_github_backup(label="auto"):
    """Async wrapper for github_backup with error handling"""
    import asyncio
    try:
        await asyncio.to_thread(github_backup, label)
    except Exception as e:
        print(f"[backup] ✗ Async wrapper failed: {e}")