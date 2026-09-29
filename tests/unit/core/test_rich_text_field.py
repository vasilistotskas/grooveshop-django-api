import re

import pytest
from django.conf import settings
from django.contrib.staticfiles import finders
from django.core.exceptions import ValidationError
from django.db import transaction
from django.urls import reverse
from rest_framework.test import APIClient
from tinymce.widgets import AdminTinyMCE

from blog.factories.post import BlogPostFactory
from core.fields.rich_text import validate_rich_text
from core.utils.sanitize import sanitize_html
from user.factories.account import UserAccountFactory

YOUTUBE = (
    '<p><iframe src="https://www.youtube.com/embed/abc123" width="560" '
    'height="314" allowfullscreen="1"></iframe></p>'
)
VIDEO_FILE = (
    '<video controls="controls"><source src="https://static.example/a.mp4" '
    'type="video/mp4"></video>'
)


# Measured from the bundled TinyMCE with the plugins enabled in
# settings.TINYMCE_DEFAULT_CONFIG. Each of these used to be stripped on
# save while the editor kept showing it.
@pytest.mark.parametrize(
    "html",
    [
        YOUTUBE,
        "<p><s>struck</s></p>",
        (
            '<table style="border-collapse: collapse; width: 100%;" border="1">'
            '<colgroup><col style="width: 50%;"><col style="width: 50%;">'
            '</colgroup><caption>c</caption><thead><tr><th scope="col">h</th>'
            "<th>h</th></tr></thead><tbody><tr><td>a</td><td>b</td></tr></tbody>"
            "<tfoot><tr><td>f</td><td>f</td></tr></tfoot></table>"
        ),
        (
            '<p><a title="t" href="https://example.com" target="_blank" '
            'rel="noopener">link</a></p>'
        ),
        '<ol style="list-style-type: lower-alpha;"><li>a</li></ol>',
        # An FAQ item as the editor saves it, measured on staging: closed,
        # the summary's class and the editing-only answer wrapper dropped.
        (
            '<details class="mce-accordion">\n<summary>Question?</summary>\n'
            "<p>Answer with <strong>formatting</strong>.</p>\n</details>"
        ),
    ],
)
def test_editor_output_is_accepted(html):
    validate_rich_text(html)


def test_video_file_is_refused_with_the_embed_hint():
    with pytest.raises(ValidationError) as exc:
        validate_rich_text(VIDEO_FILE)

    codes = {error.code for error in exc.value.error_list}
    assert codes == {"unsupported_markup", "unsupported_embed"}
    assert exc.value.error_list[0].params == {"markup": "<source>, <video>"}


def test_embed_from_another_host_names_the_dropped_src():
    with pytest.raises(ValidationError) as exc:
        validate_rich_text(
            '<iframe src="https://www.dailymotion.com/embed/video/x"></iframe>'
        )

    assert exc.value.error_list[0].params == {"markup": "<iframe src>"}


def test_script_is_refused_without_the_embed_hint():
    with pytest.raises(ValidationError) as exc:
        validate_rich_text('<p onclick="x()">a</p><script>x()</script>')

    assert [error.code for error in exc.value.error_list] == [
        "unsupported_markup"
    ]
    # The handler is residue, not content: it is dropped, not reported.
    assert exc.value.error_list[0].params == {"markup": "<script>"}


def test_event_handlers_never_survive_the_save():
    assert sanitize_html('<p onclick="x()">a</p>') == "<p>a</p>"


# Pasted from Google Docs / Word / ChatGPT, as TinyMCE keeps it
# (measured against the bundled editor): the save that failed on
# /admin/blog/blogpost/82 with "<span dir>".
PASTED = (
    '<p dir="ltr" lang="el" data-start="1" data-end="9" aria-label="x" '
    'role="note" tabindex="0" class="MsoNormal"><span dir="ltr" '
    'lang="EN-US" title="t" data-darkreader-inline-color="">'
    "Τι είναι τα mAh;</span></p>"
)


def test_pasted_text_is_accepted():
    validate_rich_text(PASTED)


def test_pasted_text_keeps_direction_language_and_tooltip():
    assert sanitize_html(PASTED) == (
        '<p dir="ltr" lang="el" class="MsoNormal"><span dir="ltr" '
        'lang="EN-US" title="t">Τι είναι τα mAh;</span></p>'
    )


def test_hidden_content_stays_hidden():
    html = '<p>shown</p><p hidden="">not reviewed</p>'

    validate_rich_text(html)
    assert sanitize_html(html) == html


def test_an_image_from_a_web_page_keeps_its_src():
    html = (
        '<p><img src="https://example.com/a.jpg" '
        'srcset="https://example.com/a2.jpg 2x" alt="a"></p>'
    )

    validate_rich_text(html)
    assert sanitize_html(html) == (
        '<p><img src="https://example.com/a.jpg" alt="a"></p>'
    )


def test_an_image_left_without_a_source_is_refused():
    with pytest.raises(ValidationError) as exc:
        validate_rich_text('<p><img srcset="https://example.com/a.jpg 1x"></p>')

    assert exc.value.error_list[0].params == {"markup": "<img srcset>"}


def test_list_numbering_is_kept():
    html = '<ol reversed="" start="3"><li value="7">a</li></ol>'

    validate_rich_text(html)
    assert sanitize_html(html) == html


@pytest.mark.parametrize(
    ("html", "lost"),
    [
        ('<p><a href="javascript:alert(1)">x</a></p>', "<a href>"),
        ('<p><img src="data:image/png;base64,AAAA" alt="x"></p>', "<img src>"),
    ],
)
def test_a_link_or_image_address_that_would_be_dropped_is_refused(html, lost):
    with pytest.raises(ValidationError) as exc:
        validate_rich_text(html)

    assert exc.value.error_list[0].params == {"markup": lost}


@pytest.mark.django_db
def test_youtube_embed_survives_a_save():
    """The reported bug: post 8's videos were gone after its next save."""
    post = BlogPostFactory(slug="rich-text-embed", body=YOUTUBE)

    post.refresh_from_db()

    assert post.body == YOUTUBE


@pytest.mark.django_db
def test_unvalidated_save_refuses_instead_of_stripping():
    """A save that never validated (shell, command, seed) must not lose
    markup either: it raises, and the stored body is untouched."""
    post = BlogPostFactory(slug="rich-text-unvalidated", body=YOUTUBE)
    translation = post.translations.first()
    translation.body = VIDEO_FILE

    # Django marks the enclosing transaction for rollback when save()
    # raises; the savepoint keeps that to this one write.
    with pytest.raises(ValidationError) as exc, transaction.atomic():
        translation.save()

    assert "body" in exc.value.message_dict
    translation.refresh_from_db()
    assert translation.body == YOUTUBE


@pytest.mark.django_db
def test_unvalidated_save_still_normalises():
    post = BlogPostFactory(
        slug="rich-text-normalised",
        body='<p><a href="https://example.com">x</a></p>',
    )

    post.refresh_from_db()

    assert post.body == (
        '<p><a href="https://example.com" rel="noopener noreferrer">x</a></p>'
    )


def test_faq_items_are_saved_closed():
    """The sanitiser allows ``<details>`` but not its ``open`` attribute:
    the editor serialises every FAQ item collapsed, so a reader sees the
    questions and opens the one they want."""
    config = settings.TINYMCE_DEFAULT_CONFIG

    assert config["details_serialized_state"] == "collapsed"


def test_an_faq_item_saved_open_is_stored_closed():
    """``open`` is display state, not content: it is dropped, not
    refused, so the item is stored closed like every other."""
    html = (
        '<details class="mce-accordion" open="open">'
        "<summary>Q</summary><p>A</p></details>"
    )

    validate_rich_text(html)
    assert sanitize_html(html) == (
        '<details class="mce-accordion"><summary>Q</summary><p>A</p></details>'
    )


_GREEK_LABELS = "admin/js/tinymce_i18n_el.js"


def _static_text(path):
    return open(finders.find(path), encoding="utf-8").read()


def test_every_accordion_label_has_a_greek_translation():
    """The bundled Greek pack leaves the accordion plugin's strings empty,
    which TinyMCE shows as a blank menu entry. Read the strings from the
    plugin itself, so a TinyMCE upgrade that adds one fails here instead
    of shipping a blank label."""
    plugin = _static_text("tinymce/plugins/accordion/plugin.min.js")
    shown = set(
        re.findall(r'(?:text|tooltip):"([^"]+)"', plugin)
        + re.findall(r'translate\("([^"]+)"', plugin)
    )
    labels = _static_text(_GREEK_LABELS)
    translated = set(re.findall(r"'([^']+)': '[^']+'", labels))

    assert shown
    assert shown <= translated


def test_greek_labels_load_between_the_pack_and_the_editor():
    """After the pack, or its empty values win; before the editor
    initialises, or its UI is already drawn."""
    scripts = list(AdminTinyMCE().media._js)

    order = [
        next(i for i, s in enumerate(scripts) if s.endswith(name))
        for name in (
            "tinymce/tinymce.min.js",
            "tinymce/langs/el.js",
            _GREEK_LABELS,
            "django_tinymce/init_tinymce.js",
        )
    ]
    assert order == sorted(order)


def test_editor_plugins_are_the_audited_set():
    """The policy covers what THESE plugins emit, measured against the
    bundled TinyMCE. A plugin added or removed here must be re-measured
    and the allowlist in core/utils/sanitize.py updated to match, or its
    output is refused on save."""
    plugins = {
        name.strip()
        for name in settings.TINYMCE_DEFAULT_CONFIG["plugins"].split(",")
    }

    assert plugins == {
        "accordion",
        "advlist",
        "anchor",
        "autolink",
        "charmap",
        "code",
        "fullscreen",
        "help",
        "image",
        "insertdatetime",
        "link",
        "lists",
        "media",
        "preview",
        "searchreplace",
        "table",
        "visualblocks",
        "wordcount",
    }


@pytest.mark.django_db
def test_translation_full_clean_refuses_unsupported_markup():
    """What parler's admin form runs on each translation."""
    post = BlogPostFactory(slug="rich-text-full-clean")
    translation = post.translations.first()
    translation.body = VIDEO_FILE

    with pytest.raises(ValidationError) as exc:
        translation.full_clean(exclude=["master"], validate_unique=False)

    assert "body" in exc.value.message_dict


@pytest.mark.django_db
def test_api_write_refuses_unsupported_markup():
    post = BlogPostFactory(slug="rich-text-api")
    client = APIClient()
    client.force_authenticate(
        UserAccountFactory(num_addresses=0, is_superuser=True, is_staff=True)
    )

    response = client.patch(
        reverse("blog-post-detail", args=[post.id]),
        {"translations": {"el": {"body": VIDEO_FILE}}},
        format="json",
    )

    assert response.status_code == 400
    post.refresh_from_db()
    assert "video" not in (post.body or "")


def _admin_form(post, body):
    """BlogPostAdmin's own change form, bound to the post's current data."""
    from django.contrib.admin.sites import site
    from django.forms.models import model_to_dict
    from django.test import RequestFactory

    from blog.models.post import BlogPost

    model_admin = site._registry[BlogPost]
    request = RequestFactory().post("/")
    request.user = UserAccountFactory(
        num_addresses=0, is_superuser=True, is_staff=True
    )
    form_class = model_admin.get_form(request, post)
    unbound = form_class(instance=post)
    data = {**model_to_dict(post), **unbound.initial, "body": body}
    data = {
        name: [getattr(v, "pk", v) for v in value]
        if isinstance(value, list | tuple)
        else getattr(value, "pk", value)
        for name, value in data.items()
        if name in unbound.fields and value is not None
    }
    return form_class(data=data, instance=post)


@pytest.mark.django_db
def test_admin_form_keeps_a_youtube_embed():
    post = BlogPostFactory(slug="rich-text-admin-ok")

    form = _admin_form(post, YOUTUBE)

    assert form.is_valid(), form.errors
    form.save()
    post.refresh_from_db()
    assert post.body == YOUTUBE


@pytest.mark.django_db
def test_admin_form_names_what_it_refuses():
    post = BlogPostFactory(slug="rich-text-admin-refused")

    form = _admin_form(post, VIDEO_FILE)

    assert not form.is_valid()
    assert "<source>, <video>" in form.errors["body"][0]
