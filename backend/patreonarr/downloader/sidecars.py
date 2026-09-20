"""Write post.json / post.html / post.md next to the downloaded media."""

from __future__ import annotations

import html
import json
from pathlib import Path

from markdownify import markdownify

from patreonarr.db.models import Creator, Post


def _front_matter(post: Post, creator: Creator) -> str:
    def q(v: object) -> str:
        return json.dumps("" if v is None else str(v))

    lines = [
        "---",
        f"title: {q(post.title)}",
        f"creator: {q(creator.name)}",
        f"campaign_id: {q(creator.campaign_id)}",
        f"post_id: {q(post.post_id)}",
        f"url: {q(post.url)}",
        f"published_at: {q(post.published_at.isoformat() if post.published_at else None)}",
        f"post_type: {q(post.post_type)}",
        "---",
        "",
    ]
    return "\n".join(lines)


def write_text_sidecars(post_dir: Path, post: Post, creator: Creator) -> list[Path]:
    post_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    json_path = post_dir / "post.json"
    json_path.write_text(json.dumps(post.raw_json or {}, indent=2, ensure_ascii=False), "utf-8")
    written.append(json_path)

    content = post.content_html or ""
    html_path = post_dir / "post.html"
    html_path.write_text(
        '<!doctype html>\n<html><head><meta charset="utf-8">'
        f"<title>{html.escape(post.title)}</title></head>\n<body>\n"
        f"<h1>{html.escape(post.title)}</h1>\n"
        f'<p><a href="{html.escape(post.url or "")}">{html.escape(post.url or "")}</a></p>\n'
        f"{content}\n</body></html>\n",
        "utf-8",
    )
    written.append(html_path)

    md_path = post_dir / "post.md"
    body = markdownify(content, heading_style="ATX") if content else ""
    md_path.write_text(
        _front_matter(post, creator) + f"# {post.title}\n\n{body}".rstrip() + "\n", "utf-8"
    )
    written.append(md_path)
    return written
