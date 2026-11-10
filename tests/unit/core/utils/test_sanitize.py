import unittest

from hypothesis import given
from hypothesis import strategies as st

from core.utils.sanitize import (
    drop_empty_headings,
    has_visible_content,
    is_allowed_embed_url,
    lost_content,
    sanitize_html,
)


class TestDropEmptyHeadings(unittest.TestCase):
    def test_drops_the_heading_the_editor_left_behind(self):
        # As stored on webside's power bank description.
        html = '<p>a</p>\n<h2 id="πόσο"></h2>\n<p>b</p>'

        self.assertEqual(drop_empty_headings(html), "<p>a</p>\n<p>b</p>")

    def test_drops_a_heading_holding_only_formatting_and_space(self):
        html = "<h3><span><strong>&nbsp;</strong></span><br></h3><p>a</p>"

        self.assertEqual(drop_empty_headings(html), "<p>a</p>")

    def test_keeps_a_heading_with_text(self):
        html = '<h2 id="a"><span style="font-size: 14pt;">Τίτλος</span></h2>'

        self.assertEqual(drop_empty_headings(html), html)

    def test_keeps_a_heading_holding_an_image(self):
        html = '<h2><img src="https://static.example/a.png"></h2>'

        self.assertEqual(drop_empty_headings(html), html)

    def test_reads_each_heading_on_its_own(self):
        html = "<h2></h2><h3>Τίτλος</h3><h4> </h4>"

        self.assertEqual(drop_empty_headings(html), "<h3>Τίτλος</h3>")

    def test_an_empty_heading_is_not_content_the_save_refuses(self):
        self.assertEqual(lost_content('<h2 id="a"></h2><p>b</p>'), [])


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


class TestSanitizeHtmlTypography(unittest.TestCase):
    """A store's typeface and type size are its theme's, not the text's."""

    def test_drops_the_typeface_and_size_pasted_text_carries(self):
        # Webside's post 82: Arial at 14pt drew the body at 18.7 px.
        self.assertEqual(
            sanitize_html(
                '<p style="font-family: arial; font-size: 14pt;'
                ' color: #333; text-align: justify">x</p>'
            ),
            '<p style="color: #333; text-align: justify;">x</p>',
        )

    def test_drops_the_font_shorthand_and_word_properties(self):
        self.assertEqual(
            sanitize_html(
                '<span style="font: 12px/1.5 Roboto; mso-ansi-language: EL;'
                " MSO-BIDI-FONT-FAMILY: 'Times'; margin-left: 4px\">x</span>"
            ),
            '<span style="margin-left: 4px;">x</span>',
        )

    def test_keeps_properties_that_only_start_like_a_dropped_one(self):
        html = '<p style="font-weight: bold; font-style: italic">x</p>'

        self.assertEqual(sanitize_html(html), html)

    def test_a_style_of_only_typography_is_dropped(self):
        self.assertEqual(
            sanitize_html('<summary style="font-size: 15pt;">Q</summary>'),
            "<summary>Q</summary>",
        )

    def test_reads_a_semicolon_inside_a_url_or_a_string_as_part_of_it(self):
        self.assertEqual(
            sanitize_html(
                "<p style=\"background: url('a;b.png'); font-size: 9pt;"
                " content: ';'\">x</p>"
            ),
            "<p style=\"background: url('a;b.png'); content: ';';\">x</p>",
        )

    def test_drops_words_classes_and_keeps_the_authors(self):
        self.assertEqual(
            sanitize_html('<p class="MsoNormal intro">x</p>'),
            '<p class="intro">x</p>',
        )
        self.assertEqual(
            sanitize_html('<a class="msocomanchor" href="#c1">[1]</a>'),
            '<a rel="noopener noreferrer" href="#c1">[1]</a>',
        )

    def test_keeps_a_class_that_only_starts_like_words(self):
        html = '<p class="msonry-grid mso">x</p>'

        self.assertEqual(sanitize_html(html), html)

    def test_reads_a_comment_the_way_a_browser_does(self):
        # A browser skips a comment anywhere between tokens.
        for style in (
            "/**/font-size: 14pt; color: red",
            "font-size/**/: 14pt; color: red",
            "color: red; /* note */ font-family: arial",
            "color: red /* it's */; font-size: 14pt",
        ):
            with self.subTest(style=style):
                self.assertEqual(
                    sanitize_html(f'<p style="{style}">x</p>'),
                    '<p style="color: red;">x</p>',
                )

    def test_dropping_a_declaration_never_uncomments_another(self):
        self.assertEqual(
            sanitize_html(
                '<p style="font: /*; position: fixed; */ 12px a;'
                ' color: red">x</p>'
            ),
            '<p style="color: red;">x</p>',
        )

    def test_keeps_a_comment_mark_inside_a_string(self):
        html = "<p style=\"content: '/*'; color: red\">x</p>"

        self.assertEqual(sanitize_html(html), html)

    def test_drops_a_property_name_spelled_with_an_escape(self):
        self.assertEqual(
            sanitize_html(
                r'<p style="font-s\69ze: 14pt; \66ont-family: arial;'
                ' color: red">x</p>'
            ),
            '<p style="color: red;">x</p>',
        )

    def test_dropping_typography_is_not_content_the_save_refuses(self):
        self.assertEqual(
            lost_content(
                '<p class="MsoNormal" style="font-family: arial">x</p>'
            ),
            [],
        )


# Values each attribute can take, chosen so every branch of the policy is
# reached: kept as written, rewritten by the attribute filter, dropped by
# the filter, dropped by the allowlist, and ``rel`` that nh3 sets itself.
_ATTRIBUTE_VALUES = {
    "class": ["MsoNormal", "msocomanchor", "intro", "MsoNormal intro"],
    "style": [
        "color: red",
        "font-size: 14pt",
        "font-family: arial; color: red",
        "--darkreader-inline-color: #fff",
        "/* c */ font: 12px a; margin: 0",
    ],
    "rel": ["nofollow", "noopener noreferrer"],
    "href": ["#c1", "/p/1", "https://example.com/"],
    "src": [
        "https://www.youtube.com/embed/x",
        "https://evil.example/x",
        "/media/a.png",
    ],
    "id": ["_anchor_2"],
    "title": ["t"],
    "target": ["_blank"],
    "data-x": ["1"],
    "onclick": ["alert(1)"],
}
_TAGS = ["a", "p", "span", "img", "iframe", "div", "video"]


@st.composite
def _element(draw) -> str:
    tag = draw(st.sampled_from(_TAGS))
    names = draw(
        st.lists(st.sampled_from(sorted(_ATTRIBUTE_VALUES)), unique=True)
    )
    attributes = "".join(
        f' {name}="{draw(st.sampled_from(_ATTRIBUTE_VALUES[name]))}"'
        for name in names
    )
    return f"<{tag}{attributes}>x</{tag}>"


class TestSanitizeHtmlIsAFixedPoint(unittest.TestCase):
    """A second pass changes nothing, so ``normalise_rich_text`` settles in
    one run and a row is not rewritten on a save that edited nothing."""

    def test_a_link_whose_filter_drops_an_attribute_is_stable(self):
        for html in (
            '<a class="msocomanchor" id="_anchor_2" href="#_msocom_2">[1]</a>',
            '<a class="MsoX" rel="x" href="/a">b</a>',
            '<a href="/a" class="MsoX" title="t">b</a>',
            '<a style="font-size: 3px" href="/a" id="i">b</a>',
        ):
            with self.subTest(html=html):
                once = sanitize_html(html)

                self.assertEqual(sanitize_html(once), once)
                self.assertIn('rel="noopener noreferrer"', once)

    def test_replaces_the_rel_an_editor_typed(self):
        self.assertEqual(
            sanitize_html('<a href="/a" rel="nofollow">b</a>'),
            '<a href="/a" rel="noopener noreferrer">b</a>',
        )

    @given(st.lists(_element(), min_size=1, max_size=4))
    def test_any_markup_is_stable_after_one_pass(self, elements):
        once = sanitize_html("".join(elements))

        self.assertEqual(sanitize_html(once), once)


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
