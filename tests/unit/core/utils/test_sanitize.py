import unittest

from core.utils.sanitize import (
    has_visible_content,
    is_allowed_embed_url,
    sanitize_html,
)


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


class TestSanitizeHtmlStyleResidue(unittest.TestCase):
    def test_drops_custom_properties_a_browser_extension_left(self):
        cleaned = sanitize_html(
            '<span style="color: #3364ff; --darkreader-inline-color: #75a0ff;'
            ' text-decoration: underline;" data-darkreader-inline-color="">'
            "x</span>"
        )

        self.assertEqual(
            cleaned,
            '<span style="color: #3364ff; text-decoration: underline;">x'
            "</span>",
        )

    def test_a_style_of_only_custom_properties_is_dropped(self):
        self.assertEqual(
            sanitize_html('<p style="--darkreader-inline-color: red;">x</p>'),
            "<p>x</p>",
        )

    def test_a_leading_custom_property_is_dropped_too(self):
        self.assertEqual(
            sanitize_html('<p style="--a: 1; margin: 0">x</p>'),
            '<p style="margin: 0;">x</p>',
        )

    def test_a_style_without_one_is_left_as_written(self):
        html = '<p style="text-align: center">x</p>'

        self.assertEqual(sanitize_html(html), html)


class TestHasVisibleContent(unittest.TestCase):
    def test_empty_markup_shows_nothing(self):
        for html in (
            None,
            "",
            "<p></p>",
            "<p>&nbsp;</p>",
            "<p> &#160; </p>",
            "<p>&#x20;</p>",
            "<p>&#32;&#32;</p>",
            "<p>&#x0A;&#9;&nbsp;&#xa0;</p>",
            "<p>&#x20;<br>&#x20;</p>",
        ):
            with self.subTest(html=html):
                self.assertFalse(has_visible_content(html))

    def test_text_or_embedded_media_shows_something(self):
        for html in (
            "<p>a</p>",
            "text",
            "<p>&#x41;</p>",
            '<p><img src="a.png"></p>',
        ):
            with self.subTest(html=html):
                self.assertTrue(has_visible_content(html))

    def test_hidden_content_shows_nothing(self):
        for html in (
            "<p hidden>Draft</p>",
            '<div hidden=""><p>a</p><img src="a.png"></div>',
            "<div hidden><div>x</div></div><p>&nbsp;</p>",
        ):
            with self.subTest(html=html):
                self.assertFalse(has_visible_content(html))

    def test_a_hidden_element_does_not_count_itself(self):
        for html in (
            '<img hidden src="a.png">',
            '<img hidden="" src="a.png">',
            '<iframe hidden src="https://www.youtube.com/embed/x"></iframe>',
            '<p><img hidden src="a.png"></p>',
        ):
            with self.subTest(html=html):
                self.assertFalse(has_visible_content(html))

    def test_an_unhidden_sibling_of_a_hidden_element_counts(self):
        self.assertTrue(
            has_visible_content('<img hidden src="a.png"><img src="b.png">')
        )

    def test_visible_content_beside_hidden_content_counts(self):
        self.assertTrue(has_visible_content("<p hidden>a</p><p>b</p>"))
        self.assertTrue(
            has_visible_content('<div hidden><p>a</p></div><img src="a.png">')
        )

    def test_media_without_a_source_shows_nothing(self):
        for html in (
            "<img>",
            '<img src="" alt="a">',
            "<iframe></iframe>",
            '<p><iframe width="560"></iframe></p>',
        ):
            with self.subTest(html=html):
                self.assertFalse(has_visible_content(html))

    def test_media_with_a_source_shows_something(self):
        for html in (
            '<iframe src="https://www.youtube.com/embed/x"></iframe>',
            '<img src="a.png">',
        ):
            with self.subTest(html=html):
                self.assertTrue(has_visible_content(html))
