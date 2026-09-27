import unittest

from core.utils.sanitize import is_allowed_embed_url, sanitize_html


class TestSanitizeHtmlEmbeds(unittest.TestCase):
    def test_keeps_tinymce_youtube_embed(self):
        html = (
            '<p><iframe src="https://www.youtube.com/embed/abc123" '
            'width="560" height="314" allowfullscreen="allowfullscreen">'
            "</iframe></p>"
        )
        self.assertEqual(sanitize_html(html), html)

    def test_keeps_vimeo_and_nocookie_embeds(self):
        for src in (
            "https://player.vimeo.com/video/123",
            "https://www.youtube-nocookie.com/embed/abc123",
        ):
            with self.subTest(src=src):
                self.assertIn(
                    f'src="{src}"',
                    sanitize_html(f'<iframe src="{src}"></iframe>'),
                )

    def test_strips_src_of_non_embed_origin(self):
        cleaned = sanitize_html('<iframe src="https://evil.example/"></iframe>')
        self.assertEqual(cleaned, "<iframe></iframe>")

    def test_strips_srcdoc_and_event_handlers(self):
        cleaned = sanitize_html(
            '<iframe src="https://www.youtube.com/embed/x" '
            'srcdoc="<script>alert(1)</script>" onload="alert(1)"></iframe>'
        )
        self.assertNotIn("srcdoc", cleaned)
        self.assertNotIn("onload", cleaned)
        self.assertIn('src="https://www.youtube.com/embed/x"', cleaned)


class TestIsAllowedEmbedUrl(unittest.TestCase):
    def test_rejects_lookalike_and_non_https_urls(self):
        for src in (
            "https://www.youtube.com.evil.example/embed/x",
            "https://evil.example/?u=https://www.youtube.com",
            "https://www.youtube.com@evil.example/embed/x",
            "http://www.youtube.com/embed/x",
            "//www.youtube.com/embed/x",
            "/embed/x",
            "javascript:alert(1)",
            "",
        ):
            with self.subTest(src=src):
                self.assertFalse(is_allowed_embed_url(src))

    def test_accepts_listed_origin_case_insensitively(self):
        self.assertTrue(is_allowed_embed_url("https://WWW.YouTube.com/embed/x"))
