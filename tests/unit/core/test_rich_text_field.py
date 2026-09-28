import pytest
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.urls import reverse
from rest_framework.test import APIClient

from blog.factories.post import BlogPostFactory
from core.fields.rich_text import validate_rich_text
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
        # An FAQ item, as the accordion plugin saves it (collapsed).
        (
            '<details class="mce-accordion"><summary class="mce-accordion-summary">'
            'Question?</summary><div class="mce-accordion-body"><p>Answer.</p>'
            "</div></details>"
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
    assert exc.value.error_list[0].params == {"markup": "<p onclick>, <script>"}


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
    the editor must serialise every FAQ item collapsed, or a save that
    left one open would be refused."""
    config = settings.TINYMCE_DEFAULT_CONFIG

    assert config["details_serialized_state"] == "collapsed"


def test_an_faq_item_saved_open_is_refused():
    with pytest.raises(ValidationError) as exc:
        validate_rich_text(
            '<details class="mce-accordion" open="open">'
            "<summary>Q</summary><p>A</p></details>"
        )

    assert exc.value.error_list[0].params == {"markup": "<details open>"}


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
