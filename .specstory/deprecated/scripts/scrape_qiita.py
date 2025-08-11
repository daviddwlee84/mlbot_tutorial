#!/usr/bin/env python3
import asyncio
import os
import re
from dataclasses import dataclass
from typing import List, Optional
from datetime import datetime, timezone

import httpx
from aiolimiter import AsyncLimiter
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator
from markdownify import markdownify as md

# Fallback: trafilatura/readability can be heavy; we'll parse with BeautifulSoup first

WORKSPACE_ROOT = "/Users/daviddwlee84/Documents/Program/Personal/mlbot_tutorial"
DOCS_README = os.path.join(WORKSPACE_ROOT, "docs/README.md")
OUTPUT_DIR = os.path.join(WORKSPACE_ROOT, "docs")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}

RATE_LIMIT = AsyncLimiter(2, 1)  # 2 requests per second

@dataclass
class LinkItem:
    title: str
    url: str
    parent: Optional[str] = None


def read_links_from_docs_readme() -> List[LinkItem]:
    links: List[LinkItem] = []
    with open(DOCS_README, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line.startswith("-"):
                continue
            m = re.search(r"\[(.*?)\]\((https?://[^\)]+)\)", line)
            if not m:
                continue
            title, url = m.group(1), m.group(2)
            indent = len(line) - len(line.lstrip())
            parent = None
            if indent > 0 and links:
                parent = links[0].title
            links.append(LinkItem(title=title, url=url, parent=parent))
    return links


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[\s/]+", "-", value)
    value = re.sub(r"[^a-z0-9\-]+", "", value)
    value = re.sub(r"-+", "-", value)
    return value.strip("-") or "page"


def extract_article_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    article = soup.select_one("article")
    if not article:
        # Fallback: Qiita's content container
        article = soup.select_one("div.it-MdContent") or soup
    # Remove non-content tags
    for tag in article.find_all(["script", "style", "nav", "header", "footer", "aside"]):
        tag.decompose()
    return str(article)


def html_to_markdown_text(html: str) -> str:
    article_html = extract_article_html(html)
    text_md = md(article_html, heading_style="ATX")
    # Collapse excessive blank lines
    text_md = re.sub(r"\n{3,}", "\n\n", text_md)
    return text_md.strip()


async def fetch_html(client: httpx.AsyncClient, url: str) -> str:
    async with RATE_LIMIT:
        resp = await client.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        return resp.text


def translate_text(text: str, source: str = "ja", target: str = "zh-TW") -> str:
    if not text.strip():
        return text
    translator = GoogleTranslator(source=source, target=target)
    # Split text into paragraphs, then chunk to avoid length limits
    paragraphs = text.split("\n\n")
    translated_blocks: List[str] = []
    for block in paragraphs:
        block = block.strip()
        if not block:
            translated_blocks.append("")
            continue
        chunks: List[str] = []
        current: List[str] = []
        current_len = 0
        for line in block.split("\n"):
            # keep reasonable safety margin for API
            if current_len + len(line) + 1 > 3500:
                chunks.append("\n".join(current))
                current = [line]
                current_len = len(line)
            else:
                current.append(line)
                current_len += len(line) + 1
        if current:
            chunks.append("\n".join(current))
        translated_chunk_list = []
        for chunk in chunks:
            try:
                translated_chunk_list.append(translator.translate(chunk))
            except Exception:
                translated_chunk_list.append(chunk)
        translated_blocks.append("\n".join(translated_chunk_list))
    return "\n\n".join(translated_blocks)


def get_title_from_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        return h1.get_text(strip=True)
    title_tag = soup.find("title")
    if title_tag and title_tag.get_text(strip=True):
        return title_tag.get_text(strip=True)
    return ""


def qiita_id_from_url(url: str) -> str:
    return url.rstrip("/").split("/")[-1]


async def process_link(client: httpx.AsyncClient, link: LinkItem) -> None:
    html = await fetch_html(client, link.url)
    ja_title = get_title_from_html(html) or link.title
    md_text = html_to_markdown_text(html)

    zh_title = translate_text(ja_title, source="ja", target="zh-TW") if ja_title else ""
    zh_text = translate_text(md_text, source="ja", target="zh-TW")

    item_id = qiita_id_from_url(link.url)
    filename = f"qiita_{item_id}.md"
    output_path = os.path.join(OUTPUT_DIR, filename)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("---\n")
        f.write(f"title: {zh_title}\n")
        if ja_title:
            f.write(f"title_ja: {ja_title}\n")
        f.write(f"source: {link.url}\n")
        f.write(f"source_id: {item_id}\n")
        f.write("language: zh-TW\n")
        f.write("origin_language: ja\n")
        f.write(f"fetched_at: {datetime.now(timezone.utc).isoformat()}\n")
        f.write("---\n\n")
        f.write(zh_text)
    print(f"Saved: {output_path}")


async def main():
    links = read_links_from_docs_readme()
    if not links:
        print("No links found in docs/README.md")
        return

    async with httpx.AsyncClient(follow_redirects=True) as client:
        tasks = [process_link(client, link) for link in links]
        await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
