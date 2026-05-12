"""Build per-day per-account note bodies from data/headlines.json.

Output: prints JSON list of {date, account, slug, title, body} so a caller
can write each entry to basic-memory under tweets/<date>/.
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

HEADLINES = Path(__file__).parent / "data" / "headlines.json"

ACCOUNT_META = {
    "DeItaone": {"slug": "walter-bloomberg", "display": "Walter Bloomberg (@DeItaone)"},
    "jukan05": {"slug": "jukan", "display": "Jukan (@jukan05)"},
}


def build_body(account: str, date: str, posts: list[dict]) -> str:
    meta = ACCOUNT_META.get(account, {"slug": account.lower(), "display": f"@{account}"})
    posts = sorted(posts, key=lambda p: p["created_at"], reverse=True)
    lines = [f"Source: @{account} on X · Date: {date} UTC · Posts: {len(posts)}", ""]
    for p in posts:
        ts = p["created_at"][11:16]
        lines.append(f"### {ts} UTC")
        lines.append(p["text"].strip())
        lines.append(
            f"_likes: {p.get('likes', 0)} · "
            f"retweets: {p.get('retweets', 0)} · id: {p['id']}_"
        )
        lines.append("")
    return "\n".join(lines)


def main(hours: int = 72):
    with open(HEADLINES) as f:
        data = json.load(f)
    headlines = data["headlines"]

    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for h in headlines:
        date = h["created_at"][:10]
        grouped[(date, h["account"])].append(h)

    out = []
    for (date, account), posts in sorted(grouped.items(), reverse=True):
        meta = ACCOUNT_META.get(account, {"slug": account.lower(), "display": f"@{account}"})
        out.append(
            {
                "date": date,
                "account": account,
                "slug": meta["slug"],
                "title": f"{meta['display']} — {date}",
                "directory": f"tweets/{date}",
                "tags": ["tweets", meta["slug"], account, date],
                "post_count": len(posts),
                "body": build_body(account, date, posts),
            }
        )
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
