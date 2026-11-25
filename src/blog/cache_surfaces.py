"""The blog app's cache surfaces, which
``core.cache.surfaces.register_default_surfaces`` registers at boot."""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from core.cache.registry import CacheSurface
from core.cache.surfaces import (
    ACCOUNT_BOOKKEEPING_FIELDS,
    nuxt_functions,
    nuxt_handlers_matching,
    nuxt_routes,
)

SURFACES = (
    CacheSurface(
        code="blog",
        label=_("Blog"),
        description=_(
            "Blog post list/detail, related posts, comments,"
            " categories, tags, and authors."
        ),
        django_patterns=(
            "*BlogPostViewSet_*",
            "*BlogCategoryViewSet_*",
            "*BlogTagViewSet_*",
            "*BlogAuthorViewSet_*",
        ),
        # ``cache:nitro:handlers:Blog*`` prefix-matches every Nuxt
        # blog handler (BlogPostViewSet, BlogPostDetailViewSet,
        # BlogPostComments, BlogCategoryViewSet, BlogCategoryDetail,
        # BlogCategoryPostsViewSet, BlogTagViewSet, BlogTagDetail,
        # BlogAuthorViewSet, BlogAuthorDetail, BlogCommentViewSet).
        # ``nuxt_handlers_matching("Blog")``, not ``nuxt_handlers("Blog")``: the
        # latter matched zero keys (see the helper docstrings).
        # ``nuxt_routes("/blog")`` adds the rendered pages —
        # /blog, /blog/categories, /blog/category/**, /blog/post/**
        # are SWR-cached (600s).
        nuxt_patterns=nuxt_handlers_matching("Blog")
        + nuxt_functions(
            "builderDataBlogPost",
            "builderDataBlogCategory",
            "builderDataSlug",
        )
        + nuxt_routes("/blog"),
        invalidated_by=(
            "blog.BlogPost",
            "blog.BlogPostTranslation",
            "blog.BlogCategory",
            "blog.BlogCategoryTranslation",
            "blog.BlogAuthor",
            "blog.BlogAuthorTranslation",
            "blog.BlogTag",
            "blog.BlogTagTranslation",
            # Approving or hiding a comment is a moderator edit.
            "blog.BlogComment",
            "blog.BlogCommentTranslation",
            # An author's and a commenter's public identity: name and
            # avatar with its size (``UserPublicSerializer``).
            "user.UserAccount",
        ),
        # A sign-in or a loyalty update changes no public identity.
        ignored_update_fields=ACCOUNT_BOOKKEEPING_FIELDS,
        icon="article",
        group="content",
    ),
)
