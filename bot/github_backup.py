import base64, time, requests
from .config import GITHUB_TOKEN, DB_PATH

REPO   = "Nisugi/GSIV-BlueTracker"
BRANCH = "main"

def github_backup(label="auto"):
    if not GITHUB_TOKEN:
        print("[backup] GITHUB_TOKEN not set – skipping")
        return

    ts   = time.strftime("%Y%m%d-%H%M%S")
    name = f"posts-{ts}-{label}.sqlite3"

    headers = {"Authorization": f"Bearer {GITHUB_TOKEN}"}

    base_sha = requests.get(
        f"https://api.github.com/repos/{REPO}/git/refs/heads/{BRANCH}",
        headers=headers).json()["object"]["sha"]

    blob_sha = requests.post(
        f"https://api.github.com/repos/{REPO}/git/blobs",
        headers=headers,
        json={"content": base64.b64encode(DB_PATH.read_bytes()).decode(),
              "encoding": "base64"}).json()["sha"]

    tree_sha = requests.post(
        f"https://api.github.com/repos/{REPO}/git/trees",
        headers=headers,
        json={"base_tree": base_sha,
              "tree":[{"path":name,"mode":"100644","type":"blob","sha":blob_sha}]}).json()["sha"]

    commit_sha = requests.post(
        f"https://api.github.com/repos/{REPO}/git/commits",
        headers=headers,
        json={"message":f"DB backup {name}","tree":tree_sha,"parents":[base_sha]}).json()["sha"]

    requests.patch(
        f"https://api.github.com/repos/{REPO}/git/refs/heads/{BRANCH}",
        headers=headers, json={"sha":commit_sha})

    print(f"[backup] uploaded {name}")