"""Parse the public Posts tab. This is not a supported YouTube Data API."""
import json
import re
from dataclasses import dataclass


class PostsUnavailable(ValueError):
    pass


@dataclass(frozen=True)
class CommunityPost:
    id: str
    text: str
    author: str
    published: str
    image_url: str = ""

    @property
    def link(self):
        return f"https://www.youtube.com/post/{self.id}"


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def text_value(value):
    if not isinstance(value, dict):
        return ""
    return value.get("simpleText", "") or "".join(run.get("text", "") for run in value.get("runs", []))


def parse_posts_page(html, channel_id):
    match = re.search(r'(?:var\s+ytInitialData\s*=|window\["ytInitialData"\]\s*=|ytInitialData\s*=)\s*', html)
    if not match:
        raise PostsUnavailable("YouTube 沒有回傳貼文資料，可能是同意頁或存取限制")
    try:
        data, _ = json.JSONDecoder().raw_decode(html[match.end():])
    except ValueError:
        raise PostsUnavailable("YouTube 貼文資料格式已變更") from None
    tabs = [node["tabRenderer"] for node in walk(data) if "tabRenderer" in node]
    selected = next((tab for tab in tabs if tab.get("selected")), None)
    if not selected:
        raise PostsUnavailable("找不到 YouTube 貼文分頁")
    endpoint = selected.get("endpoint", {}).get("commandMetadata", {}).get("webCommandMetadata", {}).get("url", "")
    if selected.get("title") not in ("Posts", "Community", "貼文", "社群", "投稿", "コミュニティ") and not endpoint.endswith(("/posts", "/community")):
        raise PostsUnavailable("此頻道沒有可讀取的公開貼文分頁")
    posts = []
    seen = set()
    for node in walk(selected.get("content", {})):
        renderer = node.get("backstagePostRenderer") or node.get("postRenderer")
        if not renderer:
            continue
        post_id = renderer.get("postId", "")
        author_id = renderer.get("authorEndpoint", {}).get("browseEndpoint", {}).get("browseId")
        if not re.fullmatch(r"Ug[A-Za-z0-9_-]+", post_id) or post_id in seen or (author_id and author_id != channel_id):
            continue
        image_url = ""
        for attachment in walk(renderer.get("backstageAttachment", {})):
            image = attachment.get("backstageImageRenderer", {}).get("image", {}).get("thumbnails", [])
            if image:
                image_url = image[-1].get("url", "")
                break
        if image_url.startswith("//"):
            image_url = "https:" + image_url
        posts.append(CommunityPost(post_id, text_value(renderer.get("contentText")), text_value(renderer.get("authorText")), text_value(renderer.get("publishedTimeText")), image_url))
        seen.add(post_id)
    if not posts:
        messages = [text_value(node["messageRenderer"].get("text", {})).lower() for node in walk(selected.get("content", {})) if "messageRenderer" in node]
        empty_phrases = ("no posts", "hasn't posted", "has not posted", "尚未發布", "沒有貼文", "投稿はありません")
        if not any(phrase in message for message in messages for phrase in empty_phrases):
            raise PostsUnavailable("貼文結構無法識別，未更新通知基準")
    return posts
