import json
import os
import pandas as pd
from tqdm import tqdm
from collections import Counter
from bertopic import BERTopic
from sklearn.feature_extraction.text import CountVectorizer
import gc

# ========= CONFIG =========
with open("config.json", "r", encoding="utf-8") as config_file:
    config = json.load(config_file)

dataset = config[0]['dataset']
save_path = "data"
os.makedirs(save_path, exist_ok=True)

# Input/output paths
INPUT_FILE = f"{dataset}_grouped_preprocessed.jsonl"
input_path = os.path.join(save_path, os.path.basename(INPUT_FILE))
OUTPUT_JSON = os.path.join(save_path, f"{dataset}_grouped_preprocessed_wtopics.json")
OUTPUT_TOPICS = os.path.join(save_path, f"{dataset}_topics_with_keywords.csv")
OUTPUT_STATS = os.path.join(save_path, f"{dataset}_topics_stats.csv")
TOPIC_NAMES_FILE = os.path.join(save_path, f"{dataset}_topics_names.csv")
TOPIC_FILTERED_FILE = os.path.join(save_path, f"{dataset}_grouped_preprocessed_wtopics_filtered.jsonl")

# Topic modelling parameters
N_TOPICS = 100
MIN_POSTS = config[1]['threshold_topic_filtering'] # Minimum posts per topic to keep the topic
# ==========================

# ========= STEP 1: LOAD DATA =========
print("📥 Loading dataset...")
with open(input_path, "r", encoding="utf-8") as f:
    data = json.load(f)
print(f"✅ Loaded {len(data):,} posts.")

# ========= STEP 2: COLLECT TEXTS =========
docs = []
meta = []  # (type, post_idx, comment_idx)

print("\n🧩 Collecting posts and comments...")
for i, post in enumerate(tqdm(data, desc="Collecting", unit="post")):
    post_text = (post.get("title", "") + " " + post.get("selftext", "")).strip()
    if post_text:
        docs.append(post_text)
        meta.append(("post", i, None))

    comments = post.get("comments", [])
    for j, c in enumerate(comments):
        c_text = c.get("body", "") if isinstance(c, dict) else str(c).strip()
        if c_text:
            docs.append(c_text)
            meta.append(("comment", i, j))

print(f"✅ Total documents collected: {len(docs):,}")

# ========= STEP 3: TRAIN BERTopic =========
print("\n🧠 Training BERTopic model...")
vectorizer_model = CountVectorizer(stop_words="english")
topic_model = BERTopic(nr_topics=N_TOPICS, vectorizer_model=vectorizer_model, verbose=True)

topics, probs = topic_model.fit_transform(docs)
gc.collect()
print("✅ BERTopic model training completed.")

# ========= STEP 4: SAVE TOPICS INFO =========
print("\n💾 Saving topic keywords and stats...")
topic_info = topic_model.get_topic_info()

# Save top keywords per topic
rows = []
for topic_num in topic_info.Topic:
    if topic_num == -1:
        continue
    words = topic_model.get_topic(topic_num)
    keywords = [w[0] for w in words[:10]]
    rows.append({
        "topic_id": int(topic_num),
        "keywords": ", ".join(keywords)
    })
topics_df = pd.DataFrame(rows)
topics_df.to_csv(OUTPUT_TOPICS, index=False, sep=";")
print(f"✅ Topics saved to: {OUTPUT_TOPICS}")

# Assign topic IDs to posts and comments
print("\n🪄 Assigning topic_id to posts and comments...")
for (typ, post_idx, comment_idx), topic_id in tqdm(zip(meta, topics), total=len(meta), desc="Assigning"):
    tid = int(topic_id) if topic_id is not None else -1
    if typ == "post":
        data[post_idx]["topic_id"] = tid
    else:
        comments = data[post_idx].get("comments", [])
        if comment_idx < len(comments):
            c = comments[comment_idx]
            if isinstance(c, str):
                data[post_idx]["comments"][comment_idx] = {"body": c, "topic_id": tid}
            elif isinstance(c, dict):
                c["topic_id"] = tid

# Save dataset with topic IDs
with open(OUTPUT_JSON, "w", encoding="utf-8") as f_out:
    json.dump(data, f_out, ensure_ascii=False, indent=2)
print(f"✅ Updated dataset saved to: {OUTPUT_JSON}")

# ========= STEP 5: BUILD TOPIC STATISTICS =========
post_counts = Counter()
comment_counts = Counter()
for post in tqdm(data, desc="Counting posts/comments", unit="post"):
    tid = post.get("topic_id", -1)
    post_counts[tid] += 1
    for c in post.get("comments", []):
        ct = c.get("topic_id", -1) if isinstance(c, dict) else -1
        comment_counts[ct] += 1

stats_rows = []
for tid in topics_df["topic_id"]:
    row = {
        "topic_id": tid,
        "keywords": topics_df.loc[topics_df["topic_id"] == tid, "keywords"].values[0],
        "num_posts": post_counts.get(tid, 0),
        "num_comments": comment_counts.get(tid, 0),
        "total": post_counts.get(tid, 0) + comment_counts.get(tid, 0)
    }
    stats_rows.append(row)
stats_df = pd.DataFrame(stats_rows)
stats_df.to_csv(OUTPUT_STATS, index=False, sep=";")
print(f"✅ Statistics saved to: {OUTPUT_STATS}")

# ========= STEP 6: CREATE TOPIC NAMES =========
topic_names_list = []
for _, row in stats_df.iterrows():
    keywords = row['keywords'].split(",")[:3]
    name = "".join([k.strip().capitalize() for k in keywords])
    topic_names_list.append({"topic_id": row['topic_id'], "name": name})
pd.DataFrame(topic_names_list).to_csv(TOPIC_NAMES_FILE, index=False, sep=";")
print(f"✅ Topic names saved to: {TOPIC_NAMES_FILE}")

# ========= STEP 7: FILTER POSTS/COMMENTS BY THRESHOLD =========
valid_topics = set(stats_df[stats_df["num_posts"] >= MIN_POSTS]["topic_id"].astype(int))
topic_names = dict(zip([t["topic_id"] for t in topic_names_list], [t["name"] for t in topic_names_list]))

filtered_data = []
for post in tqdm(data, desc="Filtering posts by topic", unit="post"):
    post_tid = post.get("topic_id", -1)
    if post_tid not in valid_topics:
        continue
    post["author"] = topic_names.get(post_tid, post.get("author", "[deleted]"))

    new_comments = []
    for c in post.get("comments", []):
        c_tid = c.get("topic_id", -1)
        if c_tid not in valid_topics:
            continue
        c["author"] = topic_names.get(c_tid, c.get("author", "[deleted]"))
        new_comments.append(c)
    post["comments"] = new_comments
    filtered_data.append(post)

# Save filtered dataset
with open(TOPIC_FILTERED_FILE, "w", encoding="utf-8") as f_out:
    json.dump(filtered_data, f_out, ensure_ascii=False, indent=2)

print(f"\n✅ Topic-filtered dataset saved: {TOPIC_FILTERED_FILE}")
print(f" - Total posts: {len(filtered_data):,}")
print(f" - Total comments: {sum(len(p.get('comments', [])) for p in filtered_data):,}")
