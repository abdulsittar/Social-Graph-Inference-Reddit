# ========== IMPORTS ==========
import os
import json
import time
from collections import defaultdict
from pathlib import Path

import libtorrent as lt
import zstandard as zstd

import re
import glob

import datetime

# ========== CONFIG ==========
with open('config.json', 'r') as config_file:
    config = json.load(config_file)

dataset = config[0]['dataset']
start_date = config[0]['start_date']
end_date = config[0]['end_date']

reddit_torrent_file = r"torrent/reddit-1614740ac8c94505e4ecb9d88be8bed7b6afddd4.torrent"

subreddit_list = []
if dataset == "climate":
    subreddit_list = ["climate", "climatechange", "climateskeptics"]
elif dataset == "covid":
    subreddit_list = ["COVID19", "COVID19positive", "covidlonghaulers"]
elif dataset == "custom":
    subreddit_list = config[0]['custom_subreddits']
else:
    raise ValueError("Dataset not recognized. Please use 'climate' or 'covid' in config.json or use 'custom' and specify 'custom_subreddits'.")

# output folder
save_path = "data"
os.makedirs(save_path, exist_ok=True)

# estimation parameters
AUTO_PROCEED = False
REMOVE_ZST_AFTER_DECOMPRESS = True


# ========== HELPER FUNCTIONS ==========
def gb_from_bytes(b):
    """Convert bytes to gigabytes."""
    return b / (1024 ** 3)


def estimate_datapoints_million(gb):
    """Estimate number of datapoints (in millions) using linear rule ~0.8 * GB."""
    return 0.8 * gb


def pretty_print_table(summary):
    """Print summary table of estimated data sizes and datapoints."""
    hdr = f"{'subreddit':20s} | {'comments (GB)':>12s} | {'posts (GB)':>10s} | {'est. datapoints (k)':>20s}"
    print(hdr)
    print("-" * len(hdr))
    for sub, info in summary.items():
        c_gb = info.get("comments_gb")
        p_gb = info.get("posts_gb")
        est = info.get("est_m", 0.0) * 1000  # convert million to thousand
        print(f"{sub:20s} | { (f'{c_gb:.2f}' if c_gb is not None else '-'):>12s} | { (f'{p_gb:.2f}' if p_gb is not None else '-'):>10s} | {est:20.2f}")


def decompress_zst(src, dst):
    """Stream decompress a .zst file into a .jsonl file."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(src, "rb") as f_in, open(dst, "wb") as f_out:
        dctx = zstd.ZstdDecompressor()
        dctx.copy_stream(f_in, f_out)


# ========== LOAD TORRENT METADATA ==========
info = lt.torrent_info(reddit_torrent_file)
files = info.files()

print(f"\nTorrent loaded: {info.name()}")
print(f"Total files in torrent: {files.num_files()}")

index_to_path = {i: files.file_path(i).replace("\\", "/") for i in range(files.num_files())}
candidates = [
    (i, p) for i, p in index_to_path.items()
    if "/subreddits24/" in p.lower() and (p.lower().endswith("_comments.zst") or p.lower().endswith("_submissions.zst"))
]
print(f"Found {len(candidates)} reddit data files (.zst) inside torrent.\n")


# ========== SELECT FILES FOR DOWNLOAD ==========
summary = defaultdict(dict)
selected_file_indices = set()

for sub in subreddit_list:
    sub_low = sub.lower()
    comments_idx, posts_idx = None, None
    comments_gb, posts_gb = None, None

    for idx, path in candidates:
        base = os.path.basename(path).lower()
        if base == f"{sub_low}_comments.zst":
            comments_idx = idx
            comments_gb = gb_from_bytes(files.file_size(idx))
        elif base == f"{sub_low}_submissions.zst":
            posts_idx = idx
            posts_gb = gb_from_bytes(files.file_size(idx))

    summary[sub]["comments_idx"] = comments_idx
    summary[sub]["posts_idx"] = posts_idx
    summary[sub]["comments_gb"] = comments_gb
    summary[sub]["posts_gb"] = posts_gb

    est_total_m = 0.0
    if comments_gb:
        est_total_m += estimate_datapoints_million(comments_gb)
    if posts_gb:
        est_total_m += estimate_datapoints_million(posts_gb)
    summary[sub]["est_m"] = est_total_m

    if comments_idx is not None:
        selected_file_indices.add(comments_idx)
    if posts_idx is not None:
        selected_file_indices.add(posts_idx)


# ========== SHOW SUMMARY ==========
print("Summary of selected subreddits:\n")
pretty_print_table(summary)

total_gb = sum(gb_from_bytes(files.file_size(i)) for i in selected_file_indices)
total_dp = estimate_datapoints_million(total_gb) * 1000  # convert million to thousand
print(f"\nTotal to download: {total_gb:.2f} GB (~{total_dp:.2f}k datapoints)\n")

if not AUTO_PROCEED:
    proceed = input("Proceed with download? [y/N]: ").strip().lower()
    if proceed != "y":
        print("Aborted.")
        exit()
else:
    print("AUTO_PROCEED = True -> continuing download.\n")


# ========== DOWNLOAD SELECTED FILES ==========
session = lt.session()
session.listen_on(6881, 6891)

params = {"ti": info, "save_path": save_path}
handle = session.add_torrent(params)

# Set file priorities (only selected ones)
priorities = [0] * files.num_files()
for i in selected_file_indices:
    priorities[i] = 7
handle.prioritize_files(priorities)

# ========== FLATTEN DOWNLOAD PATHS ==========
# Move selected files directly into data/ folder (no reddit/subreddits24/)
for idx in selected_file_indices:
    orig_path = index_to_path[idx]
    base_name = os.path.basename(orig_path)
    new_path = base_name  # just filename
    handle.rename_file(idx, new_path)

print("Starting download...\n")
last_print = 0

try:
    while True:
        s = handle.status()
        file_progress = handle.file_progress()

        # check if all selected files are finished
        done_bytes = sum(file_progress[i] for i in selected_file_indices)
        total_bytes = sum(files.file_size(i) for i in selected_file_indices)
        progress = done_bytes / total_bytes if total_bytes > 0 else 0

        if time.time() - last_print > 1.5:
            print(
                f"\rProgress: {progress*100:6.2f}% | "
                f"Down: {s.download_rate/1024:8.1f} kB/s | "
                f"Peers: {s.num_peers:3d} | "
                f"Downloaded: {gb_from_bytes(done_bytes):6.2f} GB",
                end=""
            )
            last_print = time.time()

        if done_bytes >= total_bytes:
            print("\nDownload complete for selected files.")
            break

        time.sleep(0.3)

except KeyboardInterrupt:
    print("\nDownload interrupted by user.")
    session.pause()
    exit()


# ========== DECOMPRESS DOWNLOADED FILES ==========
print("\nDecompressing downloaded .zst files...\n")

def find_downloaded_zst(idx):
    """
    Try several ways to locate the downloaded .zst file for a given file index:
      1) flattened path: save_path / basename(internal_path)
      2) original internal path under save_path
      3) walk save_path and return first matching basename
    Returns full filesystem path or None if not found.
    """
    internal_path = index_to_path[idx]
    basename = os.path.basename(internal_path)

    # 1) flattened path where we renamed the file before download
    candidate1 = os.path.join(save_path, basename)
    if os.path.exists(candidate1):
        return candidate1

    # 2) original internal path (torrent-preserved folders)
    candidate2 = os.path.join(save_path, *internal_path.split("/"))
    if os.path.exists(candidate2):
        return candidate2

    # 3) walk the save_path to find the first file matching the basename
    for root, _, files in os.walk(save_path):
        if basename in files:
            return os.path.join(root, basename)

    # not found
    return None

for idx in selected_file_indices:
    zst_path = find_downloaded_zst(idx)
    if not zst_path:
        # give a helpful message including both expected locations
        internal_path = index_to_path[idx]
        basename = os.path.basename(internal_path)
        print(f"⚠️  Missing file for index {idx}: tried '{os.path.join(save_path, basename)}' and '{os.path.join(save_path, *internal_path.split('/'))}'")
        continue

    jsonl_path = os.path.splitext(zst_path)[0] + ".jsonl"
    print(f"→ {os.path.relpath(zst_path, start=save_path)} → {os.path.basename(jsonl_path)}")
    try:
        decompress_zst(zst_path, jsonl_path)
        if REMOVE_ZST_AFTER_DECOMPRESS:
            os.remove(zst_path)
    except Exception as e:
        print(f"   Error decompressing {zst_path}: {e}")

print("\n✅ JSONL files in:", os.path.abspath(save_path))

# ========== CLEANUP ==========
# Find and remove any .parts file created by libtorrent
def cleanup_parts_files(target_dir):
    parts_files = glob.glob(os.path.join(target_dir, ".*.parts"))
    if not parts_files:
        print("\nℹ️  No .parts files found for cleanup.")
        return

    for pf in parts_files:
        try:
            os.remove(pf)
            print(f"🧹 Removed leftover parts file: {pf}")
        except Exception as e:
            print(f"⚠️  Could not remove {pf}: {e}")

cleanup_parts_files(save_path)

# ========== MERGE & GROUP POSTS AND COMMENTS WITH DATE FILTER ==========
print("\n📦 Grouping posts and comments with date filtering...\n")

def iso_to_utc_timestamp(date_str):
    """Convert ISO date string (YYYY-MM-DD) to UTC timestamp."""
    dt = datetime.datetime.fromisoformat(date_str)
    dt = dt.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=datetime.timezone.utc)
    return int(dt.timestamp())

start_ts = iso_to_utc_timestamp(start_date)
end_ts = iso_to_utc_timestamp(end_date)
print(f"Filtering posts/comments between {start_date} ({start_ts}) and {end_date} ({end_ts})")

# --- Step 1: Collect all JSONL files for posts and comments ---
posts_files = [os.path.join(save_path, f) for f in os.listdir(save_path) if f.endswith("_submissions.jsonl")]
comments_files = [os.path.join(save_path, f) for f in os.listdir(save_path) if f.endswith("_comments.jsonl")]

posts_dict = {}
total_posts_read = total_posts_kept = 0

# --- Step 2: Read posts and filter by date ---
for pf in posts_files:
    with open(pf, "r", encoding="utf-8") as f:
        for line in f:
            total_posts_read += 1
            post = json.loads(line)
            post_id = post.get("id")
            try:
                ts = int(float(post.get("created_utc", 0)))
            except Exception:
                ts = 0
            if start_ts <= ts <= end_ts and post_id:
                posts_dict[post_id] = {
                    "id": post_id,
                    "title": post.get("title", ""),
                    "author": post.get("author", ""),
                    "score": post.get("score", 0),
                    "url": post.get("url", ""),
                    "created_utc": ts,
                    "num_comments": post.get("num_comments", 0),
                    "selftext": post.get("selftext", ""),
                    "comments": []
                }
                total_posts_kept += 1

print(f"Posts read: {total_posts_read}, kept within range: {total_posts_kept}")

# --- Step 3: Read comments and attach to corresponding post ---
total_comments_read = comments_attached = 0
for cf in comments_files:
    with open(cf, "r", encoding="utf-8") as f:
        for line in f:
            total_comments_read += 1
            comment = json.loads(line)
            parent_id = comment.get("link_id", "").replace("t3_", "")
            try:
                c_ts = int(float(comment.get("created_utc", 0)))
            except Exception:
                c_ts = 0
            if parent_id in posts_dict and start_ts <= c_ts <= end_ts:
                posts_dict[parent_id]["comments"].append({
                    "id": comment.get("id", ""),
                    "author": comment.get("author", ""),
                    "score": comment.get("score", 0),
                    "created_utc": c_ts,
                    "body": comment.get("body", "")
                })
                comments_attached += 1

print(f"Comments read: {total_comments_read}, attached to kept posts: {comments_attached}")

# --- Step 4: Save grouped dataset ---
grouped_file = os.path.join(save_path, f"{dataset}_grouped.jsonl")
with open(grouped_file, "w", encoding="utf-8") as f:
    json.dump(list(posts_dict.values()), f, ensure_ascii=False, indent=2)

print(f"\n✅ Grouped dataset saved to: {grouped_file}")
print(f"Summary:\n  Posts kept: {total_posts_kept}\n  Comments attached: {comments_attached}")

