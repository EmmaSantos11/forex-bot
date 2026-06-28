"""
cloud_sync.py — Hugging Face Hub model storage.

Uploads the models/ and data/ folders to Emmaa1/forex-ai-bot-models after
every Sunday retrain, and downloads them at bot startup when local files are
missing (e.g. fresh install or new machine).

Requires in .env:
  HF_TOKEN   = hf_...   (Write-scoped token from huggingface.co/settings/tokens)
  HF_REPO_ID = Emmaa1/forex-ai-bot-models
"""

import os
import glob as _glob

from dotenv import load_dotenv

load_dotenv()

_TOKEN   = os.getenv("HF_TOKEN", "").strip()
_REPO_ID = os.getenv("HF_REPO_ID", "Emmaa1/forex-ai-bot-models").strip()

# Folders and file patterns to sync
_UPLOAD_PATTERNS = [
    ("models/*.pkl",  "models"),   # all trained model files
    ("data/*.csv",    "data"),     # manual trade features
    ("data/*.txt",    "data"),     # user pairs list
]


def _get_api():
    """Returns an authenticated HfApi instance, or None if token not set."""
    if not _TOKEN or _TOKEN == "paste_your_new_token_here":
        print("  ⚠️  HF_TOKEN not set — cloud sync skipped.")
        return None
    try:
        from huggingface_hub import HfApi
        return HfApi(token=_TOKEN)
    except ImportError:
        print("  ⚠️  huggingface_hub not installed — run: pip install huggingface_hub")
        return None


def upload_to_hub():
    """
    Uploads all model .pkl files and data CSVs to the Hugging Face repo.
    Called automatically after every Sunday retrain.
    Skips gracefully if token not configured.
    """
    api = _get_api()
    if api is None:
        return

    uploaded = 0
    errors   = 0

    for pattern, repo_subdir in _UPLOAD_PATTERNS:
        for local_path in _glob.glob(pattern):
            filename    = os.path.basename(local_path)
            repo_path   = f"{repo_subdir}/{filename}"
            try:
                api.upload_file(
                    path_or_fileobj=local_path,
                    path_in_repo=repo_path,
                    repo_id=_REPO_ID,
                    repo_type="model",
                )
                uploaded += 1
                print(f"  ☁️  Uploaded → {repo_path}")
            except Exception as e:
                errors += 1
                print(f"  ⚠️  Upload failed ({filename}): {e}")

    if uploaded:
        print(f"  ✅ Cloud sync complete — {uploaded} file(s) uploaded to {_REPO_ID}")
    if errors:
        print(f"  ⚠️  {errors} file(s) failed — check token and repo name in .env")


def download_from_hub(force: bool = False):
    """
    Downloads model and data files from the Hugging Face repo to local disk.
    Only downloads files that are missing locally (or all files if force=True).
    Called at bot startup so a fresh install is self-healing.

    force=True: re-downloads everything regardless of local state (useful after
    moving to a new machine or after a drive failure).
    """
    api = _get_api()
    if api is None:
        return

    try:
        from huggingface_hub import hf_hub_download
        files = api.list_repo_files(repo_id=_REPO_ID, repo_type="model")
    except Exception as e:
        print(f"  ⚠️  Could not list cloud files: {e}")
        return

    downloaded = 0
    skipped    = 0

    for repo_path in files:
        # repo_path looks like "models/xgb_EURUSD.pkl" or "data/user_pairs.txt"
        local_path = repo_path   # same relative structure locally

        if not force and os.path.exists(local_path):
            skipped += 1
            continue

        # Make sure the local directory exists
        local_dir = os.path.dirname(local_path)
        if local_dir:
            os.makedirs(local_dir, exist_ok=True)

        try:
            tmp = hf_hub_download(
                repo_id=_REPO_ID,
                filename=repo_path,
                repo_type="model",
                token=_TOKEN,
                local_dir=".",          # download relative to bot root
                local_dir_use_symlinks=False,
            )
            # hf_hub_download may place the file in a cache subfolder;
            # move it to the expected local path if needed.
            if tmp and os.path.abspath(tmp) != os.path.abspath(local_path):
                import shutil
                shutil.copy2(tmp, local_path)
            downloaded += 1
            print(f"  ⬇️  Downloaded ← {repo_path}")
        except Exception as e:
            print(f"  ⚠️  Download failed ({repo_path}): {e}")

    if downloaded:
        print(f"  ✅ Cloud restore complete — {downloaded} file(s) downloaded from {_REPO_ID}")
    elif skipped:
        print(f"  ✅ All {skipped} cloud file(s) already present locally — no download needed.")


def cloud_status() -> dict:
    """
    Returns a summary dict for the dashboard or Telegram /status command.
    {
        "configured": bool,
        "repo": str,
        "remote_files": int,
        "error": str | None,
    }
    """
    if not _TOKEN or _TOKEN == "paste_your_new_token_here":
        return {"configured": False, "repo": _REPO_ID, "remote_files": 0, "error": "HF_TOKEN not set"}

    api = _get_api()
    if api is None:
        return {"configured": False, "repo": _REPO_ID, "remote_files": 0, "error": "huggingface_hub not installed"}

    try:
        files = list(api.list_repo_files(repo_id=_REPO_ID, repo_type="model"))
        return {"configured": True, "repo": _REPO_ID, "remote_files": len(files), "error": None}
    except Exception as e:
        return {"configured": False, "repo": _REPO_ID, "remote_files": 0, "error": str(e)}
