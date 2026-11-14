"""The admin: Unfold (store and platform), its links and TinyMCE."""

from os import getenv

from django.templatetags.static import static
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _

from settings.application import ROOT_URLCONF

DJANGO_ADMIN_FORCE_ALLAUTH = (
    getenv("DJANGO_ADMIN_FORCE_ALLAUTH", "True") == "True"
)

# ─────────────────────────────────────────────────────────────────────
# Platform control-plane admin (platform.grooveshop.space).
#
# A SEPARATE Unfold config, selected by
# ``PlatformAdminSite.settings_name``. ``unfold.settings.get_config()``
# merges this over ``CONFIG_DEFAULTS``, so only the differences from a
# stock Unfold install are declared here — everything absent falls back
# to Unfold's own defaults.
#
# Deliberately does NOT reuse UNFOLD below: that block carries tenant
# #1's branding and a sidebar of per-store sections, neither of which
# belongs on the control plane.
# ─────────────────────────────────────────────────────────────────────

# The documentation site's tenant-admin guide, linked from every admin
# page's footer as "Help & Guides". Env-gated like the ops links below so
# a local checkout renders no link at all rather than one to a site that
# may not carry this build's pages yet; production sets the platform
# docs host in backend-config.
ADMIN_DOCS_URL = getenv("ADMIN_DOCS_URL", "").strip()

# Platform ops consoles, listed in both admins' site menu for
# platform superusers only, and only when the URL is configured. In dev,
# set ADMIN_FLOWER_URL=http://localhost:5556 etc. in `.env`; production
# usually does not expose these surfaces at all.
ADMIN_OPS_LINKS = [
    {
        "icon": entry["icon"],
        "title": entry["title"],
        "link": entry["link"],
        "attrs": {"target": "_blank", "rel": "noopener"},
    }
    for entry in [
        {
            "icon": "monitoring",
            "title": _("Flower (Celery)"),
            "link": getenv("ADMIN_FLOWER_URL", "").strip(),
        },
        {
            "icon": "mark_email_unread",
            "title": _("Mailpit"),
            "link": getenv("ADMIN_MAILPIT_URL", "").strip(),
        },
        {
            "icon": "search",
            "title": _("Meilisearch"),
            "link": getenv("ADMIN_MEILISEARCH_URL", "").strip(),
        },
        {
            "icon": "router",
            "title": _("RabbitMQ"),
            "link": getenv("ADMIN_RABBITMQ_URL", "").strip(),
        },
    ]
    if entry["link"]
]

# The language switcher both admin sites show: one entry per ``LANGUAGES``
# code. ``UNFOLD_PLATFORM`` is merged over Unfold's defaults, not over
# ``UNFOLD``, so a key set only on ``UNFOLD`` is absent on the control
# plane; the platform admin had no switcher for that reason.
ADMIN_LANGUAGE_FLAGS = {
    "el": "🇬🇷",
    "en": "🇬🇧",
}

UNFOLD_PLATFORM = {
    "SHOW_LANGUAGES": True,
    "LANGUAGE_FLAGS": ADMIN_LANGUAGE_FLAGS,
    "SITE_TITLE": _("Platform Admin"),
    "SITE_HEADER": "GrooveShop Platform",
    "SITE_SUBHEADER": _("Control plane"),
    "SITE_SYMBOL": "hub",
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": False,
    # The ops consoles, for platform superusers (admin/navigation.py).
    "SITE_DROPDOWN": "admin.navigation.ops_site_dropdown",
    "ENVIRONMENT": "admin.permissions.platform_environment",
    # ⌘K command palette history. What the palette finds is decided by
    # admin.search.CommandSearchMixin, not by this block.
    "COMMAND": {
        "show_history": True,
    },
    # The project's Tailwind build and admin tweaks, as on the store
    # admin: the dashboard, billing page and footer templates use
    # classes Unfold's own stylesheet does not ship.
    "STYLES": [
        lambda request: static("css/styles.css"),
        lambda request: static("css/admin.css"),
    ],
    # Built per request, like the store's (admin/navigation.py).
    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": False,
        "navigation": "admin.navigation.platform_sidebar",
    },
}


UNFOLD = {
    "SITE_TITLE": getenv("UNFOLD_SITE_TITLE", "GrooveShop Admin"),
    "SITE_HEADER": getenv("UNFOLD_SITE_HEADER", "GrooveShop"),
    "SITE_SUBHEADER": getenv("UNFOLD_SITE_SUBHEADER", "Commerce control"),
    "SITE_SYMBOL": "storefront",
    # The store's own storefront ("View site" in the account menu).
    "SITE_URL": "admin.navigation.storefront_url",
    # Each store's own marks; see ``admin/branding.py`` for the rule.
    "SITE_ICON": "admin.branding.site_icon",
    "SITE_LOGO": "admin.branding.site_logo",
    "SITE_FAVICONS": "admin.branding.site_favicons",
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": True,
    "SHOW_BACK_BUTTON": True,
    "BORDER_RADIUS": "0.625rem",
    "ENVIRONMENT": "admin.environment.environment_callback",
    # Prefixes the browser-tab title (e.g. "[PROD]") so staff never
    # edit the wrong environment by mistake. Both are silent for a
    # store's own staff in production (admin/environment.py).
    "ENVIRONMENT_TITLE_PREFIX": (
        "admin.environment.environment_title_prefix_callback"
    ),
    "COLORS": {
        "base": {
            "50": "oklch(98.5% 0.002 247.839)",
            "100": "oklch(96.7% 0.003 264.542)",
            "200": "oklch(92.8% 0.006 264.531)",
            "300": "oklch(87.2% 0.010 258.338)",
            "400": "oklch(70.7% 0.022 261.325)",
            "500": "oklch(55.1% 0.027 264.364)",
            "600": "oklch(44.6% 0.030 256.802)",
            "700": "oklch(37.3% 0.034 259.733)",
            "800": "oklch(27.8% 0.033 256.848)",
            "900": "oklch(21.0% 0.034 264.665)",
            "950": "oklch(13.0% 0.028 261.692)",
        },
        "primary": {
            "50": "oklch(96.2% 0.018 272.314)",
            "100": "oklch(93.0% 0.034 272.788)",
            "200": "oklch(87.0% 0.065 274.039)",
            "300": "oklch(78.5% 0.115 274.713)",
            "400": "oklch(67.3% 0.182 276.935)",
            "500": "oklch(58.5% 0.233 277.117)",
            "600": "oklch(51.1% 0.262 276.966)",
            "700": "oklch(45.7% 0.240 277.023)",
            "800": "oklch(39.8% 0.195 277.366)",
            "900": "oklch(35.9% 0.144 278.697)",
            "950": "oklch(25.7% 0.090 281.288)",
        },
        "font": {
            "subtle-light": "var(--color-base-500)",
            "subtle-dark": "var(--color-base-400)",
            "default-light": "var(--color-base-700)",
            "default-dark": "var(--color-base-200)",
            "important-light": "var(--color-base-900)",
            "important-dark": "var(--color-base-50)",
        },
    },
    "SHOW_LANGUAGES": True,
    "LANGUAGE_FLAGS": ADMIN_LANGUAGE_FLAGS,
    # ⌘K / Ctrl+K command palette history. What the palette finds is
    # decided by admin.search.CommandSearchMixin, not by this block.
    "COMMAND": {
        "show_history": True,
    },
    "STYLES": [
        lambda request: static("css/styles.css"),
        lambda request: static("css/admin.css"),
    ],
    # Loads on every admin page. ``tinymce_save_sync.js`` patches the
    # missing django-tinymce ↔ unfold form-submit handoff so that
    # rich-text edits in HTMLField (Product description, BlogPost body,
    # etc.) are actually copied from the TinyMCE iframe to the underlying
    # <textarea> before the form is POSTed. Without it the save returns
    # 302 success and django-simple-history writes a row, but the field
    # value is unchanged because the form body has the page-load
    # textarea value, not the edited iframe content.
    "SCRIPTS": [
        lambda request: static("admin/js/tinymce_save_sync.js"),
    ],
    # Built per request (admin/navigation.py): each entry is listed only
    # for a user its admin page would open for, and only while the
    # store's plan and settings have the feature it belongs to.
    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": False,
        "navigation": "admin.navigation.store_sidebar",
    },
    # Cross-model tab strips that appear on related changelist / detail
    # pages. Lets staff hop across linked admins (Order → Invoice;
    # BoxNow shipment → locker → events) without bouncing through the
    # sidebar. Tabs gated on `is_superuser` are hidden for staff.
    "TABS": [
        {
            "models": [
                "order.order",
                "order.orderitem",
                "order.invoice",
                "order.retailcredit",
                "order.stocklog",
                "order.orderhistory",
                "order.orderitemhistory",
            ],
            "items": [
                {
                    "title": _("Orders"),
                    "link": reverse_lazy(
                        "admin:order_order_changelist", urlconf=ROOT_URLCONF
                    ),
                },
                # Line items have their own changelist (filter by
                # product: who bought it) but no sidebar entry.
                {
                    "title": _("Order Items"),
                    "link": reverse_lazy(
                        "admin:order_orderitem_changelist",
                        urlconf=ROOT_URLCONF,
                    ),
                },
                {
                    "title": _("Receipts"),
                    "link": reverse_lazy(
                        "admin:order_invoice_changelist", urlconf=ROOT_URLCONF
                    ),
                },
                {
                    "title": _("Credit Documents"),
                    "link": reverse_lazy(
                        "admin:order_retailcredit_changelist",
                        urlconf=ROOT_URLCONF,
                    ),
                },
                {
                    "title": _("Stock Logs"),
                    "link": reverse_lazy(
                        "admin:order_stocklog_changelist", urlconf=ROOT_URLCONF
                    ),
                    "permission": "admin.permissions.is_superuser",
                },
                {
                    "title": _("Order History"),
                    "link": reverse_lazy(
                        "admin:order_orderhistory_changelist",
                        urlconf=ROOT_URLCONF,
                    ),
                    "permission": "admin.permissions.is_superuser",
                },
            ],
        },
        {
            "models": [
                "shipping_boxnow.boxnowshipment",
                "shipping_boxnow.boxnowlocker",
                "shipping_boxnow.boxnowparcelevent",
            ],
            "items": [
                {
                    "title": _("BoxNow Shipments"),
                    "link": reverse_lazy(
                        "admin:shipping_boxnow_boxnowshipment_changelist",
                        urlconf=ROOT_URLCONF,
                    ),
                },
                {
                    "title": _("BoxNow Lockers"),
                    "link": reverse_lazy(
                        "admin:shipping_boxnow_boxnowlocker_changelist",
                        urlconf=ROOT_URLCONF,
                    ),
                },
                {
                    "title": _("BoxNow Events"),
                    "link": reverse_lazy(
                        "admin:shipping_boxnow_boxnowparcelevent_changelist",
                        urlconf=ROOT_URLCONF,
                    ),
                    "permission": "admin.permissions.is_superuser",
                },
            ],
        },
        {
            "models": [
                "shipping_acs.acsshipment",
                "shipping_acs.acsstation",
                "shipping_acs.acspickuplist",
                "shipping_acs.acspickuplistrun",
                "shipping_acs.acscodpayout",
                "shipping_acs.acstrackingevent",
                "shipping_acs.acsapicall",
            ],
            "items": [
                {
                    "title": _("ACS Shipments"),
                    "link": reverse_lazy(
                        "admin:shipping_acs_acsshipment_changelist",
                        urlconf=ROOT_URLCONF,
                    ),
                },
                {
                    "title": _("ACS Stations"),
                    "link": reverse_lazy(
                        "admin:shipping_acs_acsstation_changelist",
                        urlconf=ROOT_URLCONF,
                    ),
                },
                {
                    "title": _("ACS Pickup Lists"),
                    "link": reverse_lazy(
                        "admin:shipping_acs_acspickuplist_changelist",
                        urlconf=ROOT_URLCONF,
                    ),
                    "permission": "admin.permissions.is_superuser",
                },
                {
                    "title": _("ACS Pickup List Runs"),
                    "link": reverse_lazy(
                        "admin:shipping_acs_acspickuplistrun_changelist",
                        urlconf=ROOT_URLCONF,
                    ),
                },
                {
                    "title": _("ACS COD Payouts"),
                    "link": reverse_lazy(
                        "admin:shipping_acs_acscodpayout_changelist",
                        urlconf=ROOT_URLCONF,
                    ),
                    "permission": "admin.permissions.is_superuser",
                },
                {
                    "title": _("ACS Tracking Events"),
                    "link": reverse_lazy(
                        "admin:shipping_acs_acstrackingevent_changelist",
                        urlconf=ROOT_URLCONF,
                    ),
                    "permission": "admin.permissions.is_superuser",
                },
                {
                    "title": _("ACS API Calls"),
                    "link": reverse_lazy(
                        "admin:shipping_acs_acsapicall_changelist",
                        urlconf=ROOT_URLCONF,
                    ),
                    "permission": "admin.permissions.is_superuser",
                },
            ],
        },
        {
            "models": [
                "product.attribute",
                "product.attributevalue",
                "product.productvariantgroup",
            ],
            "items": [
                {
                    "title": _("Attributes"),
                    "link": reverse_lazy(
                        "admin:product_attribute_changelist",
                        urlconf=ROOT_URLCONF,
                    ),
                },
                {
                    "title": _("Attribute Values"),
                    "link": reverse_lazy(
                        "admin:product_attributevalue_changelist",
                        urlconf=ROOT_URLCONF,
                    ),
                },
                {
                    "title": _("Variant Groups"),
                    "link": reverse_lazy(
                        "admin:product_productvariantgroup_changelist",
                        urlconf=ROOT_URLCONF,
                    ),
                },
            ],
        },
        {
            "models": [
                "blog.blogpost",
                "blog.blogcategory",
                "blog.blogauthor",
                "blog.blogcomment",
                "blog.blogtag",
            ],
            "items": [
                {
                    "title": _("Blog Posts"),
                    "link": reverse_lazy(
                        "admin:blog_blogpost_changelist", urlconf=ROOT_URLCONF
                    ),
                },
                {
                    "title": _("Categories"),
                    "link": reverse_lazy(
                        "admin:blog_blogcategory_changelist",
                        urlconf=ROOT_URLCONF,
                    ),
                },
                {
                    "title": _("Authors"),
                    "link": reverse_lazy(
                        "admin:blog_blogauthor_changelist", urlconf=ROOT_URLCONF
                    ),
                },
                {
                    "title": _("Comments"),
                    "link": reverse_lazy(
                        "admin:blog_blogcomment_changelist",
                        urlconf=ROOT_URLCONF,
                    ),
                },
                {
                    "title": _("Tags"),
                    "link": reverse_lazy(
                        "admin:blog_blogtag_changelist", urlconf=ROOT_URLCONF
                    ),
                },
            ],
        },
    ],
    # The ops consoles, for platform superusers (admin/navigation.py).
    "SITE_DROPDOWN": "admin.navigation.ops_site_dropdown",
}

TINYMCE_DEFAULT_CONFIG = {
    # The editor is the self-hosted bundle shipped by django-tinymce,
    # used under TinyMCE's GPLv2+ licence. Without this TinyMCE runs in
    # "evaluation mode" and logs a warning on every admin page with an
    # editor. https://www.tiny.cloud/docs/tinymce/latest/license-key/
    "license_key": "gpl",
    "theme": "silver",
    "height": 500,
    "width": 960,
    "menubar": "file edit view insert format tools table help",
    "plugins": "advlist,autolink,lists,link,image,charmap,preview,anchor,"
    "searchreplace,visualblocks,code,fullscreen,insertdatetime,media,table,"
    "code,help,wordcount,accordion",
    # TinyMCE 7 names, each one registered by the core or a plugin above
    # (the bundle is 7.8). The 5.x ``fontselect fontsizeselect
    # formatselect`` and the premium or unloaded items that stood here
    # rendered nothing; ``blocks`` is what ``formatselect`` became.
    "toolbar": "undo redo | blocks | bold italic underline strikethrough | "
    "alignleft aligncenter alignright alignjustify | outdent indent | "
    "numlist bullist | forecolor backcolor removeformat | charmap | "
    "image media link anchor accordion | fullscreen preview code",
    # The default Format menu of 7.8 without ``fontfamily fontsize``: a
    # store's typeface and type size are its theme's (``docs/rich-text.md``).
    "menu": {
        "format": {
            "title": "Format",
            "items": "bold italic underline strikethrough superscript "
            "subscript codeformat | styles blocks align lineheight | "
            "forecolor backcolor | language | removeformat",
        },
    },
    # The same declarations the save drops (``core/utils/sanitize.py``),
    # stripped by the editor too, so a paste shows the author what will
    # be stored.
    "invalid_styles": "font font-family font-size",
    # Custom upload handler instead of `images_upload_url`. The built-in
    # uploader sends FormData with cookies (via `images_upload_credentials`)
    # but never attaches an `X-CSRFToken` header, so Django's
    # CsrfViewMiddleware rejects the request with 403 before reaching
    # `core.views.upload_image`. The handler below reads the csrftoken
    # cookie (CSRF_COOKIE_HTTPONLY = False) and forwards it as the
    # `X-CSRFToken` header. `init_tinymce.js` evals the string when it
    # contains a `(`, so we inline a function literal here.
    "images_upload_handler": (
        "function(blobInfo, progress) {"
        " return new Promise(function(resolve, reject) {"
        "  var formData = new FormData();"
        "  formData.append('file', blobInfo.blob(), blobInfo.filename());"
        "  var csrf = (document.cookie.match(/(?:^|;\\s*)csrftoken=([^;]+)/) || [])[1] || '';"
        "  fetch('/upload_image', {"
        "   method: 'POST', credentials: 'include',"
        "   headers: {'X-CSRFToken': csrf, 'X-Requested-With': 'XMLHttpRequest'},"
        "   body: formData"
        "  }).then(function(r) {"
        "   if (!r.ok) { return r.text().then(function(t) { throw new Error('HTTP ' + r.status + ': ' + t); }); }"
        "   return r.json();"
        "  }).then(function(d) {"
        "   if (d && d.location) { resolve(d.location); }"
        "   else { reject({message: (d && d['Error Message']) || 'Upload failed', remove: true}); }"
        "  }).catch(function(e) { reject({message: e.message || 'Upload failed', remove: true}); });"
        " });"
        "}"
    ),
    # FAQ items (the accordion plugin's <details>/<summary>): open while
    # the author writes, saved closed, so a reader sees the questions
    # and taps the one they want. Saved closed also means ``open`` never
    # reaches the stored HTML, so the sanitiser does not allow it.
    "details_initial_state": "expanded",
    "details_serialized_state": "collapsed",
    "relative_urls": False,
    "remove_script_host": False,
    "entity_encoding": "raw",
}

TINYMCE_COMPRESSOR = False
# Between ``tinymce.min.js`` and ``init_tinymce.js`` (django-tinymce's
# widget media): the bundled Greek pack, then the labels it leaves
# empty, so the editor's Greek UI has no blank entries. See the header
# of static/admin/js/tinymce_i18n_el.js for why the order matters.
TINYMCE_EXTRA_MEDIA = {
    "js": ["tinymce/langs/el.js", "admin/js/tinymce_i18n_el.js"],
}
