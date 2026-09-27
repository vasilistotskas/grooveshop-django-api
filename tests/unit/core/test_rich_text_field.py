import pytest
from django.core.exceptions import ValidationError
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
def test_unvalidated_save_is_still_sanitised():
    post = BlogPostFactory(
        slug="rich-text-unvalidated",
        body="<p>kept</p><script>alert(1)</script>",
    )

    post.refresh_from_db()

    assert post.body == "<p>kept</p>"


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
