import json
import unittest

from services.youtube_posts import PostsUnavailable, parse_posts_page


CHANNEL = "UCwUvX4_nrbYGhlRxqJIB3JA"


def page(content, title="Posts", prefix="var ytInitialData = "):
    return prefix + json.dumps({"contents": {"tabs": [{"tabRenderer": {"title": title, "selected": True, "content": content}}]}}, ensure_ascii=False) + ";</script>"


def renderer(post_id="Ugtest", author=CHANNEL):
    return {"postId": post_id, "authorEndpoint": {"browseEndpoint": {"browseId": author}}, "authorText": {"runs": [{"text": "作者"}]}, "contentText": {"runs": [{"text": "hello } ;\n"}, {"text": "world"}]}, "publishedTimeText": {"simpleText": "2 hours ago"}, "backstageAttachment": {"postMultiImageRenderer": {"images": [{"backstageImageRenderer": {"image": {"thumbnails": [{"url": "https://example.com/small.png"}, {"url": "//example.com/large.png"}]}}}]}}}


class PostParserTests(unittest.TestCase):
    def test_text_images_duplicate_ids_and_foreign_posts(self):
        contents = [{"backstagePostRenderer": renderer()}, {"postRenderer": renderer()}, {"backstagePostRenderer": renderer("Ugforeign", "UCother")}, {"backstagePostRenderer": renderer("Ugsecond")}]
        posts = parse_posts_page(page(contents), CHANNEL)
        self.assertEqual([post.id for post in posts], ["Ugtest", "Ugsecond"])
        self.assertEqual(posts[0].text, "hello } ;\nworld")
        self.assertEqual(posts[0].image_url, "https://example.com/large.png")
        self.assertEqual(posts[0].link, "https://www.youtube.com/post/Ugtest")

    def test_window_assignment_and_recognizable_empty_tab(self):
        self.assertEqual(parse_posts_page(page({"messageRenderer": {"text": {"simpleText": "No posts"}}}, prefix='window["ytInitialData"] = '), CHANNEL), [])

    def test_unknown_layout_consent_and_redirect_must_not_become_empty_baseline(self):
        for html in ("<html>Consent</html>", page({"unexpected": []}), page({"messageRenderer": {}}, title="Videos"), page({"messageRenderer": {"text": {"simpleText": "Service unavailable"}}}), "var ytInitialData = broken;"):
            with self.subTest(html=html), self.assertRaises(PostsUnavailable):
                parse_posts_page(html, CHANNEL)
