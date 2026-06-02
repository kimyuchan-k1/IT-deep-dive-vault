#!/usr/bin/env python3
"""Rebuild index/graph.json and index/embeddings.sqlite from posts/*.md.

graph.json  : nodes (every slug referenced) + edges (related + body [[wikilinks]]).
embeddings.sqlite : post metadata table. Vector column left NULL when no embedding
                    API is configured; a later run can backfill (see WORKFLOW guardrail).

Usage: python scripts/update_index.py
"""
import os, re, json, sqlite3, glob

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POSTS = os.path.join(ROOT, "posts")
INDEX = os.path.join(ROOT, "index")
os.makedirs(INDEX, exist_ok=True)


def parse(path):
    txt = open(path, encoding="utf-8").read()
    fm = re.search(r"^---\n(.*?)\n---", txt, re.S)
    meta, body = {}, txt
    if fm:
        body = txt[fm.end():]
        for key in ("title", "date", "day", "category", "difficulty"):
            m = re.search(rf"^{key}:\s*(.+)$", fm.group(1), re.M)
            if m:
                meta[key] = m.group(1).strip()
        rel = re.search(r"^related:\s*\[(.*?)\]", fm.group(1), re.M)
        meta["related"] = re.findall(r"\[\[([a-z0-9-]+)\]\]", rel.group(1)) if rel else []
    slug = re.sub(r"^\d{4}-\d{2}-\d{2}-", "", os.path.basename(path)[:-3])
    meta["slug"] = slug
    meta["wikilinks"] = sorted(set(re.findall(r"\[\[([a-z0-9-]+)\]\]", body)))
    return meta


def main():
    posts = [parse(p) for p in sorted(glob.glob(os.path.join(POSTS, "*.md")))]
    published = {p["slug"] for p in posts}

    nodes, edges, seen = {}, [], set()
    for p in posts:
        nodes[p["slug"]] = {
            "id": p["slug"], "title": p.get("title", p["slug"]),
            "category": p.get("category", "?"), "published": True,
            "day": int(p["day"]) if p.get("day", "").isdigit() else None,
        }
    for p in posts:
        for tgt in set(p["related"]) | set(p["wikilinks"]):
            if tgt == p["slug"]:
                continue
            if tgt not in nodes:
                nodes[tgt] = {"id": tgt, "title": tgt, "category": "?", "published": False, "day": None}
            k = (p["slug"], tgt)
            if k not in seen:
                seen.add(k)
                edges.append({"source": p["slug"], "target": tgt})

    graph = {"nodes": list(nodes.values()), "edges": edges}
    with open(os.path.join(INDEX, "graph.json"), "w", encoding="utf-8") as f:
        json.dump(graph, f, ensure_ascii=False, indent=2)

    db = sqlite3.connect(os.path.join(INDEX, "embeddings.sqlite"))
    db.execute("""CREATE TABLE IF NOT EXISTS posts(
        slug TEXT PRIMARY KEY, title TEXT, category TEXT, day INTEGER,
        difficulty INTEGER, vector BLOB)""")
    for p in posts:
        db.execute(
            "INSERT OR REPLACE INTO posts(slug,title,category,day,difficulty,vector) "
            "VALUES(?,?,?,?,?,COALESCE((SELECT vector FROM posts WHERE slug=?),NULL))",
            (p["slug"], p.get("title"), p.get("category"),
             int(p["day"]) if p.get("day", "").isdigit() else None,
             int(p["difficulty"]) if p.get("difficulty", "").isdigit() else None,
             p["slug"]))
    db.commit()
    db.close()

    print(f"graph: {len(nodes)} nodes ({len(published)} published), {len(edges)} edges")
    print(f"embeddings.sqlite: {len(posts)} posts (vectors NULL - reindex when API available)")


if __name__ == "__main__":
    main()
