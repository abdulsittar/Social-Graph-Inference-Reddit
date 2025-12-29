import json
import os
from collections import defaultdict, Counter

# ========= CONFIG =========
with open('config.json', 'r', encoding='utf-8') as config_file:
    config = json.load(config_file)

INPUT_FILE = f"{config[0]['dataset']}_grouped.jsonl"
save_path = "data"

# Make sure save_path exists
os.makedirs(save_path, exist_ok=True)

# ========= STEP 1: LOAD DATA =========
input_path = os.path.join(save_path, INPUT_FILE)
with open(input_path, "r", encoding="utf-8") as f:
    data = json.load(f)

# ========= STEP 2: BUILD AUTHOR COMMENT TEXTS =========
author_texts = defaultdict(list)

for post in data:
    for comment in post.get("comments", []):
        author = comment.get("author")
        body = comment.get("body", "")
        if author and author not in ("[deleted]", "[removed]"):
            author_texts[author].append(body.strip())

# ========= STEP 3: DETECT REPETITIVE PATTERNS =========
n_words = 15

suspected_bots = set()
for author, texts in author_texts.items():
    chunk_counts = Counter()
    for text in texts:
        words = text.split()
        for i in range(len(words) - n_words + 1):  # n_words chunks
            chunk = " ".join(words[i:i + n_words])
            chunk_counts[chunk] += 1

    if any(count > 10 for count in chunk_counts.values()):
        suspected_bots.add(author)

# ========= STEP 4: SHOW RESULTS =========
if suspected_bots:
    print("⚠️  Possible bot accounts detected:")
    for a in suspected_bots:
        print(f"   - {a}")
else:
    print("✅ No clear bot accounts detected.")
    suspected_bots = set()

# ========= STEP 5: DELETION STATS & CONFIRM =========
# Posts to delete if they have bot comments OR selftext deleted/removed
posts_to_delete = [
    post for post in data
    if any(c.get("author") in suspected_bots for c in post.get("comments", []))
       or post.get("selftext") in ("[deleted]", "[removed]")
]

if posts_to_delete:
    total_posts = len(data)
    total_comments = sum(len(post.get("comments", [])) for post in data)
    comments_to_delete = sum(len(post.get("comments", [])) for post in posts_to_delete)

    print("\n📊 Deletion summary (includes all comments in deleted posts):")
    print(f" - Posts to delete: {len(posts_to_delete)} / {total_posts} "
          f"({len(posts_to_delete)/total_posts*100:.2f}%)")
    print(f" - Comments to delete: {comments_to_delete} / {total_comments} "
          f"({comments_to_delete/total_comments*100:.2f}%)")

    user_input = input("\nDo you want to remove these posts and affected comments? (y/n): ").strip().lower()
    if user_input == "y":
        data = [post for post in data if post not in posts_to_delete]
        print(f"\n🗑️  Deleted {len(posts_to_delete)} posts and {comments_to_delete} comments.")

# ========= STEP 6: TRIM COMMENTS AFTER CLEANUP =========
for post in data:
    if "comments" in post and isinstance(post["comments"], list):
        post["comments"] = post["comments"][:10]

print("\n✅ Trimmed comments to first 10 per post.")

# ========= STEP 7: SAVE PREPROCESSED DATA (as JSON array, not JSONL) =========
input_filename = os.path.basename(INPUT_FILE)
output_file = os.path.join(save_path, os.path.splitext(input_filename)[0] + "_preprocessed.jsonl")

with open(output_file, "w", encoding="utf-8") as out_f:
    json.dump(data, out_f, ensure_ascii=False, indent=2)

print("\n✅ Preprocessed data:")
print(f" - Total posts: {len(data)}")
print(f" - Total comments: {sum(len(post.get('comments', [])) for post in data)}")
print(f"\n✅ File saved as: {output_file}")
