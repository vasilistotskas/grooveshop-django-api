"""Django REST framework, request limits and the OpenAPI document."""

from os import getenv

from settings.env import DEBUG

# Rest Framework
REST_FRAMEWORK = {
    # Every error gains ``code`` (and ``fields`` for validation); see
    # core/api/exception_handler.py.
    "EXCEPTION_HANDLER": "core.api.exception_handler.exception_handler",
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "core.api.tokens.BoundedTokenAuthentication",
        # Platform staff tokens — engages ONLY on the ``StaffBearer``
        # keyword and returns None otherwise, so it composes with the
        # customer authenticator above (which claims ``Bearer`` and
        # raises on anything it doesn't recognise once engaged; two
        # ``Bearer`` authenticators cannot coexist). Resolves and
        # stamps PUBLIC-schema identities; see docs/api-staff-identity.md.
        "tenant.api_tokens.PlatformStaffTokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": [
        # Anonymous GET/HEAD/OPTIONS stay allowed so public catalog
        # endpoints work without per-viewset overrides; writes require
        # authentication. ViewSets needing anonymous writes (guest
        # checkout, contact form) set permission_classes = [AllowAny].
        #
        # Tenant scoping is NOT enforced here. Customers live in their
        # tenant's own schema, so an authenticated request on this host
        # is by construction a customer of THIS store — see
        # tenant/membership.py. The previous default additionally
        # required a UserTenantMembership, which no shopper can hold
        # (public-schema table, FK to the public user table), so every
        # authenticated write was refused.
        "rest_framework.permissions.IsAuthenticatedOrReadOnly",
    ],
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    # One trusted reverse proxy (Traefik) fronts the app - DRF must key
    # anon throttling off the XFF hop that proxy appended, not the whole
    # client-controlled header (which would let callers mint a fresh
    # throttle bucket per request).
    "NUM_PROXIES": 1,
    "DEFAULT_THROTTLE_RATES": {
        "anon": None if DEBUG else "100000/day",
        "user": None if DEBUG else "150000/day",
        # Per-endpoint scopes. Applied via throttle_classes on specific views
        # (ContactCreateThrottle, PaymentAttemptThrottle, PaymentAttemptAnonThrottle
        # in core.api.throttling) — the global anon/user throttles continue to
        # apply on top.
        "contact": None if DEBUG else "5/minute",
        "feedback": None if DEBUG else "5/minute",
        # The anonymous newsletter form. Each accepted request can send
        # an email to an arbitrary address, so it is budgeted like the
        # contact form (the confirmation resend also has its own
        # 10-minute cooldown per address).
        "newsletter_subscribe": None if DEBUG else "5/minute",
        # Creating a product alert: a guest's sends a confirmation email
        # to the address it is given, so it is budgeted like the
        # newsletter form (with the same 10-minute resend cooldown per
        # alert).
        "product_alert_create": None if DEBUG else "5/minute",
        # One file per request, and each can be tens of megabytes on
        # the pod's disk until it is claimed or reaped - so this is a
        # bandwidth budget, not just an anti-spam one. Generous enough
        # for a three-file enquiry plus retries on a bad connection.
        "contact_attachment": None if DEBUG else "20/hour",
        # One-click unsubscribe POSTs arrive from a mailbox provider's
        # shared address pool, so the budget is per address, not per mail.
        "email_unsubscribe": None if DEBUG else "120/minute",
        "payment": None if DEBUG else "10/minute",
        "payment_anon": None if DEBUG else "5/minute",
        "cart_mutation": None if DEBUG else "60/minute",
        "cart_mutation_anon": None if DEBUG else "30/minute",
        # Each mint hands out a credential for the shopper's cart.
        "cart_agent_token": None if DEBUG else "10/minute",
        # The store-admin agent surface, per staff member and store. An AI
        # app answering one question may call several tools in a row.
        "agent_staff": None if DEBUG else "120/minute",
        # Order creation moves stock, can mint a courier voucher and can
        # open a provider payment session — and guest checkout means the
        # anonymous budget is the one that matters.
        "order_create": None if DEBUG else "20/minute",
        "order_create_anon": None if DEBUG else "10/minute",
        # Coupon apply is a code-guessing oracle — keep the budget tight.
        "coupon_apply": None if DEBUG else "10/minute",
        # Gift-card balance check exposes a bearer secret's validity.
        "gift_card_check": None if DEBUG else "6/minute",
        # Business-profile submit can trigger an outbound VIES check.
        "b2b_profile_submit": None if DEBUG else "5/minute",
        "search": None if DEBUG else "120/minute",
        # Clicks get their own budget - the anonymous click endpoint must
        # not be able to starve the search allowance for the same client.
        "search_click": None if DEBUG else "60/minute",
        # Impression/click events from the suggestion strips. One
        # impression per strip render plus clicks; generous, but a
        # budget of its own so a scripted client cannot starve search.
        "recommendation_event": None if DEBUG else "120/minute",
        "view_count": None if DEBUG else "60/hour",
        "viva_return": None if DEBUG else "30/minute",
        # Public proxies to rate-limited carrier partner APIs.
        "acs_address": None if DEBUG else "30/minute",
        "boxnow_nearest": None if DEBUG else "10/minute",
        # A specimen receipt is a WeasyPrint render on the request worker.
        # A person opens one or two; the demo store's shared logins pool
        # every visitor into one bucket, so the budget leaves room for a
        # few at once without letting one client keep workers busy.
        "order_specimen": None if DEBUG else "10/minute",
        # Staff token minting (tenant.staff_api) — a credential-guessing
        # surface on the platform host, so budget accordingly.
        "staff_login": None if DEBUG else "10/hour",
        # The storefront's server trades a builder ticket for a session
        # (``builder.session_api``). One caller (our own storefront) for
        # every store, so the budget is not the per-person one above; a
        # ticket is 256 random bits, so this bounds load, not guessing.
        "builder_session": None if DEBUG else "120/minute",
    },
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "core.filters.camel_case_ordering.CamelCaseOrderingFilter",
        "rest_framework.filters.SearchFilter",
    ],
    "DEFAULT_SCHEMA_CLASS": "core.api.schema.AutoSchema",
    "DEFAULT_PAGINATION_CLASS": "core.pagination.page_number.PageNumberPaginator",
    "PAGE_SIZE": 12,
    "DEFAULT_RENDERER_CLASSES": (
        "djangorestframework_camel_case.render.CamelCaseJSONRenderer",
        *(
            (
                "djangorestframework_camel_case.render.CamelCaseBrowsableAPIRenderer",
            )
            if DEBUG
            else ()
        ),
    ),
    "DEFAULT_PARSER_CLASSES": (
        "djangorestframework_camel_case.parser.CamelCaseFormParser",
        "djangorestframework_camel_case.parser.CamelCaseMultiPartParser",
        "djangorestframework_camel_case.parser.CamelCaseJSONParser",
    ),
    "COERCE_DECIMAL_TO_STRING": False,
}

# Other general settings
APPEND_SLASH = getenv("APPEND_SLASH", "False") == "True"

DATA_UPLOAD_MAX_NUMBER_FIELDS = 10000
DATA_UPLOAD_MAX_MEMORY_SIZE = 2621440  # 2.5 MB (Django default, made explicit)

SPECTACULAR_SETTINGS = {
    # Constants, not environment: the committed ``schema.yml`` is the API
    # contract, and a title read from ``.env`` made every machine's
    # regeneration differ (tests/unit/test_schema_info.py).
    "TITLE": "GrooveShop API",
    "DESCRIPTION": "GrooveShop API",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "COMPONENT_NO_READ_ONLY_REQUIRED": False,
    "ENFORCE_NON_BLANK_FIELDS": False,
    "ENUM_ADD_EXPLICIT_BLANK_NULL_CHOICE": True,
    "SERVE_PERMISSIONS": ["rest_framework.permissions.AllowAny"]
    if DEBUG
    else ["rest_framework.permissions.IsAuthenticated"],
    "AUTHENTICATION_WHITELIST": [
        "knox.auth.TokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "SERVE_AUTHENTICATION": [
        "knox.auth.TokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "POSTPROCESSING_HOOKS": [
        "drf_spectacular.contrib.djangorestframework_camel_case.camelize_serializer_fields",
        "drf_spectacular.hooks.postprocess_schema_enums",
        "core.api.schema.postprocess_schema_parameters_to_accept_strings",
        "core.api.schema.publish_error_schemas",
    ],
    "PREPROCESSING_HOOKS": [
        "drf_spectacular.hooks.preprocess_exclude_path_format",
    ],
    "ENUM_NAME_OVERRIDES": {
        "StaffSessionRoleEnum": "builder.staff_session.ROLES",
        "OrderStatus": "order.enum.status.OrderStatus",
        # ``PayWay.key`` and ``Order.pay_way_key`` share one vocabulary; the
        # override keeps the established ``PayWayKeyEnum`` name for both
        # instead of a second ``KeyEnum`` for the same choice set.
        "PayWayKeyEnum": "pay_way.enum.pay_way.PayWayEnum",
        # Three choice sets now sit behind fields named ``type``: the
        # BoxNow locker type, ``OrderCreateErrorDetailSerializer.type``
        # and ``OrderCancelErrorDetailSerializer.type``. Without every
        # override drf-spectacular renames the older one to a hashed
        # ``Type8dcEnum``, which bleeds into the generated frontend
        # types. ``TypeEnum`` keeps the name it already had.
        "TypeEnum": "shipping_boxnow.enum.locker_type.BoxNowLockerType",
        # The link pickers' answers: the kinds a link can point at (a subset
        # of ``ReferenceKind``) and the two kinds a page can be.
        "LinkKindEnum": "builder.link_picker.KIND_CHOICES",
        "BuilderPageKindEnum": "builder.link_picker.PAGE_KIND_CHOICES",
        # ``BuilderDocument.kind`` and ``BuilderVersion.kind`` are two choice
        # sets behind one field name; named, they do not become hashed
        # ``Kind1e3Enum``/``KindCbcEnum`` in the generated frontend types.
        "BuilderDocumentKindEnum": "builder.models.DocumentKind",
        "BuilderVersionKindEnum": "builder.models.VersionKind",
        "BuilderDocumentInvalidCodeEnum": "builder.serializers.INVALID_DOCUMENT_CODE_CHOICES",
        "BuilderRevisionConflictCodeEnum": "builder.serializers.REVISION_CONFLICT_CODE_CHOICES",
        "BuilderMediaInUseCodeEnum": "builder.serializers.MEDIA_IN_USE_CODE_CHOICES",
        "OrderCreateErrorType": "order.enum.create_error.OrderCreateErrorType",
        "OrderCancelErrorType": "order.enum.cancel_error.OrderCancelErrorType",
        "ReviewStatus": "product.enum.review.ReviewStatus",
        # ``ProductAlert.kind`` and the alert confirmation's ``kind``
        # share one vocabulary: one ``ProductAlertKindEnum``, not a hashed
        # second name for the same choice set.
        "ProductAlertKindEnum": "product.models.alert.ProductAlertKind",
        "SubscriptionStatus": "user.models.subscription.UserSubscription.SubscriptionStatus",
        # Both ``SubscriptionTopic.category`` and ``Notification.category``
        # expose a ``category`` field, and they use different enum sets
        # (SubscriptionTopic.TopicCategory vs NotificationCategoryEnum).
        # Without these overrides, drf-spectacular auto-renames the
        # colliding enum to ``CategoryXYZEnum`` which bleeds into
        # generated frontend types and breaks consumer imports on every
        # regeneration.
        "TopicCategory": "user.models.subscription.SubscriptionTopic.TopicCategory",
        "NotificationCategory": "notification.enum.NotificationCategoryEnum",
        # ``Order.document_type`` (6 values — includes fulfilment
        # documents like shipping labels / credit notes) and the
        # creation-time subset (``OrderCreateFromCartSerializer`` —
        # only RECEIPT/INVOICE) share the ``documentType`` field name.
        # Without these overrides, drf-spectacular auto-renames the
        # collider to ``DocumentType128Enum`` which bleeds into
        # generated frontend types on every regeneration.
        "OrderDocumentType": "order.enum.document_type.OrderDocumentTypeEnum",
        "OrderCreateDocumentType": "order.enum.document_type.OrderCreateDocumentTypeEnum",
        # ``BoxNowShipment.parcel_state`` and ``BoxNowParcelEvent.event_type``
        # both use ``BoxNowParcelState.choices``. Without this override,
        # drf-spectacular emits ``ParcelStateEnum`` and ``EventTypeEnum``
        # for the same choice set — a single canonical name is cleaner
        # for the generated frontend types.
        "BoxNowParcelState": "shipping_boxnow.enum.parcel_state.BoxNowParcelState",
        # ``Order.shipping_kind`` and ``AcsShipment.delivery_kind`` both
        # reuse ``ShippingKind.choices``; same fix as BoxNow above to
        # keep the generated TS type single-named.
        "ShippingKind": "shipping.enum.shipping_kind.ShippingKind",
        # ``AcsShipment.charge_type`` and the inline choices on
        # ``OrderCreateFromCartSerializer.acs_charge_type`` share the
        # same choice set; consolidate naming.
        "AcsChargeType": "shipping_acs.enum.charge_type.AcsChargeType",
        # ``OrderAttribution.source_type`` is read on every order and
        # filtered on the order list; a field-derived ``SourceTypeEnum``
        # would say nothing about what the source is of.
        "OrderSourceType": "order.enum.attribution.OrderSourceType",
    },
}
