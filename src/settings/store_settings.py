"""The per-store settings and their declared defaults.

``EXTRA_SETTINGS_DEFAULTS`` is read through
``core.store_settings.store_setting``; its validator paths are data."""

# Store settings are per-store rows read through
# ``core.store_settings.store_setting``, which falls back to the value
# declared below. django-extra-settings would otherwise answer a missing
# row with a same-named attribute of the settings first.
EXTRA_SETTINGS_FALLBACK_TO_CONF_SETTINGS = False
EXTRA_SETTINGS_DEFAULTS = [
    {
        "name": "BOXNOW_DEFAULT_COMPARTMENT_SIZE",
        "type": "int",
        "value": 1,
        "validator": (
            "shipping_boxnow.packing.validate_default_compartment_size"
        ),
        "description": (
            "BOX NOW compartment booked for products that have no "
            "length, width and height: 1 = Small, 2 = Medium, 3 = "
            "Large. With dimensions on every product the size is "
            "derived from them and this is not used; with some it is "
            "the minimum."
        ),
    },
    {
        "name": "ACS_DYNAMIC_PRICING_ENABLED",
        "type": "bool",
        "value": False,
        "description": (
            "When True the ACS carrier asks the courier API for a "
            "live shipping quote (ACS_Price_Calculation) at checkout. "
            "Falls back to ACS_SHIPPING_PRICE on any API failure so "
            "a transient outage cannot block checkout. Defaults False "
            "until ops have run end-to-end pricing tests on stage."
        ),
    },
    {
        "name": "CART_ABANDONED_HOURS",
        "type": "int",
        "value": 24,
    },
    {
        "name": "STOCK_RESERVATION_TTL_MINUTES",
        "type": "int",
        "value": 30,
        "validator": "order.stock.validate_reservation_ttl_minutes_setting",
    },
    {
        "name": "B2B_INVOICING_ENABLED",
        "type": "bool",
        "value": True,
    },
    # Wholesale program (business profiles, customer groups, price
    # lists). Merchant runtime half of the two-tier gate — the plan
    # half is Tenant.b2b_enabled. Ships dark.
    {
        "name": "B2B_WHOLESALE_ENABLED",
        "type": "bool",
        "value": False,
    },
    # Whether promotions/coupons stack ON TOP of wholesale prices.
    # Off by default: wholesale is already a negotiated price, and
    # stacking retail promos on it double-discounts silently.
    {
        "name": "B2B_ALLOW_PROMOTIONS",
        "type": "bool",
        "value": False,
    },
    # Whether wholesale-priced orders take part in the loyalty program
    # — BOTH earning and redeeming. Off by default: the points basis is
    # the RETAIL price, so a negotiated price would earn full
    # retail-basis points and redeem them for a further discount on
    # already-discounted prices — a retail program subsidizing
    # wholesale. One switch, so the two halves can never disagree.
    {
        "name": "B2B_LOYALTY_ENABLED",
        "type": "bool",
        "value": False,
    },
    {
        "name": "ORDER_AUTO_CANCEL_FAILED_PAYMENT_MINUTES",
        "type": "int",
        "value": 30,
    },
    {
        "name": "ORDER_ONLINE_PAYMENT_HOLD_MINUTES",
        "type": "int",
        "value": 30,
        "validator": "order.payment_hold.validate_hold_minutes_setting",
        "description": (
            "How long an online-payment order holds its stock while it "
            "waits for the payment. It is also how long the payment link "
            "stays payable: the provider is told the same expiry, so the "
            "order is canceled and its stock released only when the "
            "payment can no longer succeed. Between 30 and 1092 minutes "
            "(the shortest Stripe session and the longest Viva payment "
            "order); Stripe never expires a session sooner than 30."
        ),
    },
    {
        "name": "ORDER_ONLINE_PAYMENT_MAX_HOLD_MINUTES",
        "type": "int",
        "value": 60,
        "validator": "order.payment_hold.validate_max_hold_minutes_setting",
        "description": (
            "The longest an online-payment order may hold its stock in "
            "total, counted from when it was placed. Opening a new "
            "payment link (a retry) can move the hold later, never past "
            "this. Once it has passed the order can no longer be paid "
            "and the shopper places a new one from their cart. At least "
            "the hold above, and at least 31 for Stripe to open a "
            "payment page."
        ),
    },
    {
        "name": "ORDER_ONLINE_PAYMENT_GRACE_MINUTES",
        "type": "int",
        "value": 5,
        "description": (
            "How long after its payment link expires an unpaid "
            "online-payment order is kept before it is canceled, so a "
            "payment confirmation still on its way reaches an open "
            "order. A confirmation after the cancel is still booked, but "
            "needs a refund."
        ),
    },
    {
        "name": "ORDER_UNPAID_ONLINE_ALERT_THRESHOLD",
        "type": "int",
        "value": 25,
        "description": (
            "Raise an operations alert when this many online-payment "
            "orders are waiting for their payment at once: a pile-up "
            "holds stock that other shoppers cannot buy. 0 turns the "
            "alert off."
        ),
    },
    {
        "name": "ORDER_MAX_UNPAID_ONLINE_ORDERS",
        "type": "int",
        "value": 5,
        "description": (
            "How many online-payment orders still waiting for their "
            "payment one customer (the account, or the email for a "
            "guest) can have at once. Each holds its stock until it is "
            "paid or auto-canceled (ORDER_ONLINE_PAYMENT_HOLD_MINUTES), so "
            "this bounds the stock a customer can lock without paying. "
            "0 turns the limit off."
        ),
    },
    {
        "name": "GIFT_CARD_STAFF_ISSUE_LIMIT",
        "type": "decimal",
        "value": 500,
        "description": (
            "The most a staff member can put on gift cards in one step "
            "(issuing a card, or adding to a balance), in the store's "
            "currency. Above it the step needs the store's owner (or a "
            "platform superuser) and is noted on the ledger. 0 turns the "
            "limit off."
        ),
    },
    {
        "name": "GIFT_CARD_STAFF_DAILY_LIMIT",
        "type": "decimal",
        "value": 1000,
        "description": (
            "The most one staff member can put on gift cards in any "
            "rolling 24 hours (issuing cards and adding to balances "
            "together), in the store's currency. Above it the step needs "
            "the store's owner (or a platform superuser) and is noted on "
            "the ledger. 0 turns the limit off."
        ),
    },
    {
        "name": "ORDER_FOLLOW_UP_DAYS",
        "type": "int",
        "value": 7,
        "description": (
            "Flag an order for follow-up once its shopper has waited this "
            "many calendar days (store time) without a delivery: an order "
            "placed on day D is flagged from 00:00 on day D+N while it is "
            "still pending, processing or shipped. Online orders that "
            "are not paid yet are never flagged. 0 turns the flag off."
        ),
    },
    {
        "name": "CHECKOUT_ABANDONMENT_HOURS",
        "type": "int",
        "value": 2,
    },
    {
        "name": "REVIEW_REQUEST_DELAY_DAYS",
        "type": "int",
        "value": 0,
        "validator": (
            "order.review_request.validate_review_request_delay_days_setting"
        ),
        "description": (
            "Send a customer one reminder email this many days after their "
            "order was delivered, asking for a review of the products they "
            "have not reviewed yet. At most one per order, and only while "
            "product reviews are on and the customer has not opted out of "
            "marketing email. 0 is off, the default: a store chooses to send "
            "this mail. The delivery email "
            "itself always invites a review."
        ),
    },
    # Contact / email
    {
        "name": "CONTACT_EMAIL",
        "type": "string",
        "value": "",
        "description": (
            "Public contact email for the storefront footer and contact "
            "page. Empty → falls back to Tenant.contact_email (if set) "
            "or settings.INFO_EMAIL."
        ),
    },
    # Store presence (contact page, footer badge, LocalBusiness
    # schema.org). All default empty — a tenant without them simply
    # renders no hours/geo anywhere.
    {
        "name": "BUSINESS_HOURS",
        "type": "json",
        "value": {},
        "validator": "tenant.validators.validate_business_hours_setting",
        "description": (
            "Weekly opening hours as JSON: "
            '{"timezone": "<IANA zone>", "schedule": {"mon".."sun": '
            'null | {"opens": "HH:MM", "closes": "HH:MM"}}}. All seven '
            "day keys are required; null means closed that day. Drives "
            "the storefront business-hours section, the footer "
            "open/closed badge and schema.org openingHoursSpecification. "
            "Empty {} = feature off."
        ),
    },
    {
        "name": "STORE_OFFICES",
        "type": "json",
        "value": [],
        "validator": "tenant.validators.validate_store_offices_setting",
        "description": (
            "Public office/branch addresses as JSON: "
            '[{"label", "street", "area", "postal", "city", '
            '"phones": [..], "i18n": {"<locale>": {"label", "street", '
            '"area", "city"}}}]. Distinct from the INVOICE_SELLER_* '
            "seat, which is the REGISTERED address a legal notice goes "
            "to — a merchant's public offices are often neither the "
            "same place nor a single place. Drives the storefront "
            "footer's contact column and the contact page. Empty [] = "
            "feature off. ``i18n`` carries the transliterated forms; "
            "the numbers and the postcode are locale-independent."
        ),
    },
    {
        "name": "STORE_GEO_LAT",
        "type": "string",
        "value": "",
        "validator": "tenant.validators.validate_latitude_setting",
        "description": (
            "The store's latitude, in decimal degrees (e.g. 40.565129, "
            "within ±90). With STORE_GEO_LNG it is the store's location: "
            "the pin of the location map section and the coordinates "
            "of the LocalBusiness structured data. Empty means no "
            "location."
        ),
    },
    {
        "name": "STORE_GEO_LNG",
        "type": "string",
        "value": "",
        "validator": "tenant.validators.validate_longitude_setting",
        "description": (
            "The store's longitude, in decimal degrees (e.g. 22.119096, "
            "within ±180). See STORE_GEO_LAT."
        ),
    },
    # Meta Conversions API
    {
        "name": "META_CAPI_ENABLED",
        "type": "bool",
        "value": False,
        "description": (
            "Master kill switch for the server-side Meta Conversions "
            "API dispatcher. Defaults False so a fresh deploy doesn't "
            "leak unintended events when the Tenant's "
            "``meta_pixel_id`` and "
            "``meta_capi_access_token`` are still unset. Flip to True "
            "in Django admin once Meta credentials are in place and "
            "the Test Events feed in Events Manager looks right. The "
            "browser pixel is independent — it gates on the "
            "``ad_storage`` cookie consent on the storefront."
        ),
    },
    # IndexNow
    {
        "name": "INDEXNOW_ENABLED",
        "type": "bool",
        "value": False,
        "description": (
            "Tell search engines (Bing, Yandex, Naver and the others that "
            "support IndexNow) when a published product or blog post is "
            "created, changed or unpublished, instead of waiting for them "
            "to crawl it. Off by default. The changes of the last half "
            "hour go out in one notification. The store's IndexNow key "
            "file is served by the storefront once this is on."
        ),
    },
    # Storefront UI toggles
    {
        "name": "RECENTLY_VIEWED_ENABLED",
        "type": "bool",
        "value": True,
        "description": (
            "Show the 'Recently Viewed' rail on the storefront home page. "
            "The history itself is stored client-side in localStorage; "
            "this flag only controls whether the rail renders."
        ),
    },
    {
        "name": "PRODUCT_ASSURANCES_ENABLED",
        "type": "bool",
        "value": True,
        "description": (
            "Show the purchase-assurances box under the buy button on "
            "product pages: delivery days from the shipping rates, the "
            "free-shipping threshold, the payment methods and the "
            "return window. Each row hides itself when the store has "
            "no data for it. A store UI preference; the storefront "
            "fails OPEN if this setting cannot be fetched."
        ),
    },
    {
        "name": "EXTERNAL_LINKS_NEW_TAB_ENABLED",
        "type": "bool",
        "value": False,
        "description": (
            "Open every link to another site in a new tab, in the rich "
            "text of posts, pages and products. Links to the store's own "
            "pages keep what the editor chose. A store UI preference, "
            "served in TenantConfig.preferences."
        ),
    },
    {
        "name": "PRODUCT_REVIEWS_ENABLED",
        "type": "bool",
        "value": True,
        "description": (
            "Product reviews feature: the review list and write-review "
            "flow on product pages plus the review API. Turning it off "
            "404s the endpoints and hides the storefront review UI."
        ),
    },
    {
        "name": "PRODUCT_REVIEWS_PURCHASE_REQUIRED",
        "type": "bool",
        "value": True,
        "description": (
            "Only customers who bought a product may review it. Turned "
            "off, every signed-in customer may; a review from a buyer "
            "still carries the verified-purchase badge either way."
        ),
    },
    {
        "name": "BLOG_COMMENTS_ENABLED",
        "type": "bool",
        "value": True,
        "description": (
            "Blog comments (and comment likes) under blog posts. Only "
            "meaningful while the blog plan flag is on — this is the "
            "finer runtime knob for a read-only blog."
        ),
    },
    {
        "name": "FAVOURITES_ENABLED",
        "type": "bool",
        "value": True,
        "description": (
            "Product favourites/wishlist: the hearts across the "
            "storefront, the account favourites page, the API, and the "
            "favourites-driven price-drop / back-in-stock notifications "
            "in the customer's account (no emails: those are the product "
            "alerts)."
        ),
    },
    {
        "name": "NEWSLETTER_ENABLED",
        "type": "bool",
        "value": True,
        "description": (
            "Newsletter topic subscriptions: the account subscriptions "
            "page and the topic/subscription APIs. Unsubscribe and "
            "confirmation links from already-sent emails keep working "
            "regardless."
        ),
    },
    {
        "name": "FEEDBACK_ENABLED",
        "type": "bool",
        "value": True,
        "description": (
            "The storefront feedback page and its submission endpoint."
        ),
    },
    {
        "name": "CONTACT_ATTACHMENTS_ENABLED",
        "type": "bool",
        "value": False,
        "description": (
            "Let visitors attach files to the contact form. OFF by "
            "default and 404s the upload endpoint when off: this is "
            "the platform's only ANONYMOUS upload, so a store that "
            "has never asked for one does not host one. Uploads land "
            "in a PRIVATE per-tenant tree with no URL - the only "
            "reader is the staff download in the admin."
        ),
    },
    {
        "name": "CONTACT_ATTACHMENTS_MAX_COUNT",
        "type": "int",
        "value": 3,
        "description": (
            "How many files one enquiry may carry. Enforced on the "
            "submit, not just in the form."
        ),
    },
    {
        "name": "CONTACT_ATTACHMENTS_MAX_MB",
        "type": "int",
        "value": 10,
        "description": (
            "Per-file size limit in megabytes. Capped in code at 25 "
            "(AttachmentPolicy.MAX_BYTES_CEILING) - the ingress, the "
            "SSR proxy and the pod's ephemeral disk are the operator's "
            "resources, so a store cannot raise this without one."
        ),
    },
    {
        "name": "CONTACT_ATTACHMENTS_TYPES",
        "type": "string",
        "value": "application/pdf",
        "validator": "contact.attachments.validate_attachment_types_setting",
        "description": (
            "Comma-separated MIME allow-list, matched against the type "
            "the file's MAGIC BYTES say it is - never its extension or "
            "the browser's declared Content-Type. Common values: "
            "application/pdf, application/zip, image/vnd.dwg (AutoCAD "
            "DWG), image/png, image/jpeg. A format with no magic "
            "number cannot be confirmed and is always refused, so "
            "plain text and ASCII DXF must arrive inside a ZIP."
        ),
    },
    {
        "name": "CONTACT_ATTACHMENTS_RETENTION_DAYS",
        "type": "int",
        "value": 90,
        "description": (
            "Days after which an attachment's BYTES are deleted while "
            "the enquiry and the file's name, size and checksum stay "
            "(GDPR art. 5(1)(e) storage limitation without destroying "
            "the business record). 0 keeps files indefinitely. Raise "
            "it for a store that must retain tender documents."
        ),
    },
    {
        "name": "ADMIN_LOG_RETENTION_DAYS",
        "type": "int",
        "value": 730,
        "description": (
            "Days after which an entry of the admin history (who added, "
            "changed or deleted what) is DELETED. 0 keeps the whole "
            "history indefinitely. Entries already older than this are "
            "deleted on the next nightly run after you set it."
        ),
    },
    {
        "name": "EMAIL_LOG_RETENTION_DAYS",
        "type": "int",
        "value": 90,
        "validator": "emails.log.validate_retention_days_setting",
        "description": (
            "Days after which a row of the email log (which mail went to "
            "whom, in what status) is DELETED, with its provider events. "
            "The log holds a masked address and the subject, never the "
            "message body. 0 keeps the log indefinitely. Rows already "
            "older than this are deleted on the next nightly run after "
            "you set it."
        ),
    },
    {
        "name": "CONTACT_RETENTION_DAYS",
        "type": "int",
        "value": 365,
        "description": (
            "Days after which a contact enquiry or a feedback entry is "
            "DELETED, together with the files attached to the enquiry "
            "(GDPR art. 5(1)(e) storage limitation). One period for "
            "both, per store. 0 keeps them indefinitely. Entries already "
            "older than this are deleted on the next nightly run after "
            "you set it. The notification email copy in the store's own "
            "inbox is not touched."
        ),
    },
    {
        "name": "PRODUCT_ALERTS_ENABLED",
        "type": "bool",
        "value": True,
        "description": (
            "Explicit product alerts (back-in-stock / price-drop "
            "subscriptions): the alert API and the alert emails."
        ),
    },
    {
        "name": "PRICE_HISTORY_ENABLED",
        "type": "bool",
        "value": True,
        "description": (
            "The price trend on the product page: a button beside the "
            "rating opens the product's recorded consumer price over "
            "time, with its change and its lowest and highest price."
        ),
    },
    {
        "name": "AGENT_COMMERCE_ENABLED",
        "type": "bool",
        "value": True,
        "description": (
            "Merchant runtime tier of the agent-commerce gate. The "
            "effective value (this AND the Tenant plan flag) is served "
            "in TenantConfig and enforced by the agent gateway for "
            "MCP/UCP/ACP, feeds and the chatbot. Cached with the "
            "tenant-resolve payload — changes take effect on cache "
            "invalidation (automatic on save)."
        ),
    },
    {
        "name": "STAFF_AI_ACCESS_ENABLED",
        "type": "bool",
        "value": False,
        "description": (
            "Lets the store's team connect their own AI app (Claude, "
            "ChatGPT and others) to read the store through the store-admin "
            "MCP server, /mcp/admin: orders, products and stock, customers, "
            "reviews, contact messages and, by role, sales and profit. What "
            "the app reads is sent to its provider, so it stays off until "
            "the store turns it on. Off: no one can connect, and an app "
            "already connected is refused at its next call."
        ),
    },
    {
        "name": "AGENT_HOSTED_PAYMENT_ENABLED",
        "type": "bool",
        "value": True,
        "description": (
            "Merchant runtime tier of the hosted-payment selection gate. "
            "Lets an AI-agent platform choose an ONLINE payment method, "
            "so the order is placed and the buyer authorises on the "
            "PSP's page instead of re-entering the whole checkout on the "
            "web. The effective value (this AND the Tenant plan flag) is "
            "served in TenantConfig; the gateway advertises the "
            "space.grooveshop.payments.hosted_selection UCP extension "
            "and accepts its pay-way id only while it is on. "
            "Subordinate to agent commerce. Cached with the "
            "tenant-resolve payload — changes take effect on cache "
            "invalidation (automatic on save)."
        ),
    },
    {
        "name": "PRODUCT_FEEDS_ENABLED",
        "type": "bool",
        "value": True,
        "description": (
            "Catalog feed syndication (Meta / TikTok / agent feeds) "
            "served by the gateway. Subordinate to agent commerce: "
            "feeds are off whenever the agent-commerce gate is off."
        ),
    },
    {
        "name": "CATALOGUE_ENABLED",
        "type": "bool",
        "value": True,
        "description": (
            "Serve the product catalogue: the listings, the product "
            "pages and search. OFF makes every one of them 404, for a "
            "store that publishes work rather than selling goods — an "
            "engineering contractor quoting per project has a product "
            "MODEL (its systems carry real specs) and no shop. Distinct "
            "from CART_ENABLED, which leaves the catalogue browsable "
            "and only removes the buying. The storefront fails OPEN if "
            "this setting cannot be fetched."
        ),
    },
    {
        "name": "MOBILE_BOTTOM_NAV_ENABLED",
        "type": "bool",
        "value": True,
        "description": (
            "Show the mobile bottom navigation bar on the storefront. "
            "A store UI preference; the storefront fails OPEN if this "
            "setting cannot be fetched."
        ),
    },
    {
        "name": "SOCIAL_LOGIN_PROVIDERS",
        "type": "json",
        "value": ["*"],
        "validator": (
            "tenant.validators.validate_social_login_providers_setting"
        ),
        "description": (
            "Whitelist of social-login provider ids for this store. "
            '["*"] (default) = every provider configured on the '
            'platform; e.g. ["google"] = Google only; [] = social '
            "login fully off. Enforced in the allauth adapter, so it "
            "controls both the login/signup buttons and the OAuth "
            "flows themselves. Consumed server-side only."
        ),
    },
    {
        "name": "CART_ENABLED",
        "type": "bool",
        "value": True,
        "description": (
            "Show the cart chrome (navbar cart button, /cart page) on "
            "the storefront. Off for shop-dark tenants that launch as "
            "content sites before their catalogue exists. Fails OPEN "
            "if the setting cannot be fetched."
        ),
    },
    {
        "name": "STICKY_ADD_TO_CART_ENABLED",
        "type": "bool",
        "value": True,
        "description": (
            "Show the sticky add-to-cart bar on product pages once the "
            "main buy button scrolls out of view. A store UI "
            "preference; the storefront fails OPEN if this setting "
            "cannot be fetched."
        ),
    },
    {
        "name": "ACCOUNT_REVIEWS_ENABLED",
        "type": "bool",
        "value": True,
        "description": (
            'Show the "My reviews" page in the storefront account '
            "area. A store preference — product-page reviews are "
            "unaffected. Off hides the menu entry and 404s the page."
        ),
    },
    {
        "name": "CHAT_WIDGET_ENABLED",
        "type": "bool",
        "value": True,
        "description": (
            "Show the AI shopping-assistant chat widget on the "
            "storefront. Turning it off hides the launcher for every "
            "visitor shortly after (client-side check); the gateway's "
            "/chat endpoint itself stays up, so flipping back on needs "
            "no deploy."
        ),
    },
    # Loyalty system settings
    {
        "name": "LOYALTY_ENABLED",
        "type": "bool",
        "value": False,
    },
    {
        "name": "PROMOTIONS_ENABLED",
        "type": "bool",
        "value": False,
    },
    {
        # Runtime half of the recommendations gate (plan half is
        # Tenant.recommendations_enabled). Fails CLOSED like every
        # commercial surface: a store opts in, it is never on by
        # accident. See docs/recommendations-engine.md §7.
        "name": "PRODUCT_SUGGESTIONS_ENABLED",
        "type": "bool",
        "value": False,
        "description": (
            "Show suggested-product strips on the storefront (product "
            "page, cart, out-of-stock). Requires the tenant's "
            "recommendations plan flag as well."
        ),
    },
    {
        "name": "DISPATCH_CUTOFF",
        "type": "string",
        "value": "15:00",
        "validator": "tenant.validators.validate_dispatch_cutoff_setting",
        "description": (
            "Last time of day (HH:MM, store local time) an order still "
            "ships the same business day. Orders at or after it, or on "
            "a weekend / public holiday, dispatch the next business "
            "day. Drives each order's estimated delivery date and the "
            "storefront's 'order before ...' line. Empty = no cutoff."
        ),
    },
    {
        "name": "RETURN_WINDOW_DAYS",
        "type": "int",
        "value": 0,
        "validator": "tenant.validators.validate_return_window_days_setting",
        "description": (
            "Days after delivery a customer can return a product, as the "
            "return-policy page states it. The storefront gives it to "
            "Google as the store's return policy for every country it "
            "ships to. 0 = not stated: only the link to the return-policy "
            "page is given."
        ),
    },
    {
        "name": "RETURN_FEES",
        "type": "string",
        "value": "",
        "validator": "tenant.validators.validate_return_fees_setting",
        "description": (
            "Who pays to send a return back: 'free' (the store pays) or "
            "'customer' (the customer pays the return shipping). Given to "
            "Google with RETURN_WINDOW_DAYS. Empty = not stated."
        ),
    },
    {
        "name": "GIFT_CARDS_ENABLED",
        "type": "bool",
        "value": False,
    },
    {
        # Greek law caps gift-card validity at 5 years minimum for
        # consumer protection; 0 disables expiry entirely.
        "name": "GIFT_CARD_VALIDITY_DAYS",
        "type": "int",
        "value": 1825,
    },
    {
        "name": "GIFT_CARD_MIN_AMOUNT",
        "type": "decimal",
        "value": 10.00,
    },
    {
        "name": "GIFT_CARD_MAX_AMOUNT",
        "type": "decimal",
        "value": 500.00,
    },
    {
        # AADE paymentMethodDetails.type for gift-card-settled amounts.
        # No dedicated voucher code exists — accountant's choice:
        # 3 = Μετρητά (default), 5 = Επί πιστώσει.
        "name": "MYDATA_GIFT_CARD_PAYMENT_TYPE",
        "type": "int",
        "value": 3,
    },
    {
        # Days before expiry to email gift-card recipients a reminder;
        # 0 disables reminders.
        "name": "GIFT_CARD_EXPIRY_REMINDER_DAYS",
        "type": "int",
        "value": 30,
    },
    {
        "name": "LOYALTY_POINTS_FACTOR",
        "type": "decimal",
        "value": 1.0,
    },
    {
        "name": "LOYALTY_REFUND_ALLOWS_NEGATIVE_BALANCE",
        "type": "bool",
        "value": True,
        "description": (
            "When an order is refunded or canceled, take back ALL the "
            "points it earned even if the customer has spent them since: "
            "their balance goes negative, later earnings pay it off and "
            "they cannot redeem until it is positive. Off: the reversal "
            "stops at zero and the points already spent stay spent."
        ),
    },
    {
        "name": "LOYALTY_XP_PER_LEVEL",
        "type": "int",
        "value": 1000,
    },
    {
        "name": "LOYALTY_NEW_CUSTOMER_BONUS_ENABLED",
        "type": "bool",
        "value": False,
    },
    {
        "name": "LOYALTY_NEW_CUSTOMER_BONUS_POINTS",
        "type": "int",
        "value": 100,
    },
    {
        "name": "LOYALTY_REDEMPTION_RATIO_EUR",
        "type": "decimal",
        "value": 100.0,
    },
    {
        "name": "LOYALTY_REDEMPTION_RATIO_USD",
        "type": "decimal",
        "value": 100.0,
    },
    {
        "name": "LOYALTY_PRICE_BASIS",
        "type": "string",
        "value": "final_price",
    },
    {
        "name": "LOYALTY_POINTS_EXPIRATION_DAYS",
        "type": "int",
        "value": 0,
    },
    {
        "name": "LOYALTY_TIER_MULTIPLIER_ENABLED",
        "type": "bool",
        "value": False,
    },
    # Re-engagement email settings
    {
        "name": "REENGAGEMENT_EMAIL_MAX_COUNT",
        "type": "int",
        "value": 3,
    },
    {
        "name": "REENGAGEMENT_EMAIL_COOLDOWN_DAYS",
        "type": "int",
        "value": 90,
    },
    {
        "name": "INACTIVE_USER_THRESHOLD_DAYS",
        "type": "int",
        "value": 60,
    },
    # Cart cleanup settings
    {
        "name": "ABANDONED_CART_CLEANUP_DAYS",
        "type": "int",
        "value": 7,
    },
    {
        "name": "OLD_GUEST_CART_CLEANUP_DAYS",
        "type": "int",
        "value": 30,
    },
    # Search settings
    {
        "name": "SEARCH_MAX_LIMIT",
        "type": "int",
        "value": 100,
    },
    # Invoice seller info — surfaced on the generated PDF via
    # ``order.invoicing._seller_snapshot``. Defaults land blank so
    # a fresh install doesn't silently ship a plausible-but-wrong
    # legal identity; ops must fill them in via the admin Settings
    # page before issuing the first invoice in production. Keys are
    # documented in ``INVOICE_SELLER_SETTING_KEYS``.
    {
        "name": "INVOICE_SELLER_NAME",
        "type": "string",
        "value": "",
        "description": "Legal company name shown at the top of the invoice.",
    },
    {
        "name": "INVOICE_SELLER_DISTINCTIVE_TITLE",
        "type": "string",
        "value": "",
        "description": (
            "Distinctive title (διακριτικός τίτλος) the store trades "
            "under, when it differs from the legal company name. Printed "
            "under the name on receipts and credit documents, and shown "
            "on the storefront in place of the legal name when set. "
            "Blank: the legal name is used everywhere."
        ),
    },
    {
        "name": "INVOICE_SELLER_LEGAL_FORM",
        "type": "string",
        "value": "",
        "description": (
            "Legal form of the business (νομική μορφή) — e.g. ΙΚΕ, ΑΕ, "
            "ΕΠΕ, ΟΕ, ΕΕ, Ατομική Επιχείρηση. Free text rather than a "
            "fixed list because the platform is not Greece-only. "
            "N. 4919/2022 art. 22 §4 requires this on the storefront."
        ),
    },
    {
        "name": "INVOICE_SELLER_VAT_ID",
        "type": "string",
        "value": "",
        "description": "Seller tax/VAT identifier (ΑΦΜ in Greece). Required by Greek tax law.",
    },
    {
        "name": "INVOICE_SELLER_TAX_OFFICE",
        "type": "string",
        "value": "",
        "description": "Seller tax office (ΔΟΥ in Greece). Required by Greek tax law.",
    },
    {
        "name": "INVOICE_SELLER_REGISTRATION_NUMBER",
        "type": "string",
        "value": "",
        "description": "Business registry number (Αρ. Γ.Ε.ΜΗ. in Greece).",
    },
    {
        "name": "INVOICE_SELLER_BUSINESS_ACTIVITY",
        "type": "string",
        "value": "",
        "description": "Primary business activity / trade (Επάγγελμα in Greece).",
    },
    {
        "name": "INVOICE_SELLER_ADDRESS_LINE_1",
        "type": "string",
        "value": "",
        "description": "First line of the seller's registered address.",
    },
    {
        "name": "INVOICE_SELLER_ADDRESS_LINE_2",
        "type": "string",
        "value": "",
        "description": "Second line of the seller's registered address (optional).",
    },
    {
        "name": "INVOICE_SELLER_CITY",
        "type": "string",
        "value": "",
        "description": "City of the seller's registered address.",
    },
    {
        "name": "INVOICE_SELLER_POSTAL_CODE",
        "type": "string",
        "value": "",
        "description": "Postal code of the seller's registered address.",
    },
    {
        "name": "INVOICE_SELLER_COUNTRY",
        "type": "string",
        "value": "",
        "description": "Country of the seller's registered address.",
    },
    {
        "name": "INVOICE_SELLER_PHONE",
        "type": "string",
        "value": "",
        "description": "Seller contact phone number (optional).",
    },
    {
        "name": "INVOICE_SELLER_EMAIL",
        "type": "string",
        "value": "",
        "description": "Seller contact email (falls back to INFO_EMAIL).",
    },
    {
        "name": "INVOICE_SELLER_IN_LIQUIDATION",
        "type": "bool",
        "value": False,
        "description": (
            "Whether the business is in liquidation (υπό εκκαθάριση). "
            "N. 4919/2022 art. 22 §4 requires disclosing this on the "
            "storefront when it applies. Rare, hence default False."
        ),
    },
    # myDATA (AADE / IAPR) ERP channel. Retail receipts are issued through a
    # fiscal device (ΦΗΜ) or a licensed provider (ν.4308/2014 art. 12 §8,
    # §10); the ERP channel is only for a business exempt from the ΦΗΜ, so
    # issuing needs both switches below. Credentials come from the AADE
    # developer portal, separate for dev and prod.
    {
        "name": "MYDATA_ENABLED",
        "type": "bool",
        "value": False,
        "description": "Whether the platform issues this store's retail receipts (11.1) and retail credit documents (11.4) itself, over the myDATA ERP channel. Off: the store issues them in its own fiscal device (ΦΗΜ) or licensed e-invoicing provider, and the platform builds and sends nothing. The ERP channel is only for a business exempt from the ΦΗΜ (ν.4308/2014 art. 12 §8 and §10), so a store that takes real orders issues nothing until MYDATA_FHM_EXEMPTION_GROUND is filled in; a demo store, or any store outside the production deployment, may issue to the AADE sandbox without it. Invoices are never issued here: staff issue them in AADE's timologio app.",
    },
    {
        "name": "MYDATA_FHM_EXEMPTION_GROUND",
        "type": "string",
        "value": "",
        "description": "The legal ground on which this business is exempt from issuing retail receipts through a fiscal device (ΦΗΜ) or a licensed provider: the ΠΟΛ.1002/2014 provision, the accountant who confirmed it, and the date, e.g. 'ΠΟΛ.1002/2014 §<paragraph>, confirmed by <accountant>, <date>'. Online sales are not exempt as such. While this is blank the platform issues nothing over the myDATA ERP channel, even with MYDATA_ENABLED on — except test documents to the AADE sandbox (MYDATA_ENVIRONMENT 'dev') from a demo store or a store outside the production deployment, which take no real orders. Since ν.5222/2025 art. 238 (28/7/2025) exempt businesses are pointed to timologio or a licensed provider, so the accountant must also confirm the ERP channel is still allowed.",
    },
    {
        "name": "MYDATA_AUTO_SUBMIT",
        "type": "bool",
        "value": False,
        "description": "If true, the platform issues its retail documents by itself: the receipt when an order ships, a credit document when money goes back. Otherwise they are issued only through the order's admin actions.",
    },
    {
        "name": "MYDATA_ENVIRONMENT",
        "type": "string",
        "value": "dev",
        "description": "Target environment: 'dev' (the AADE sandbox, mydataapidev.aade.gr, for development and tests: its documents are not tax documents) or 'prod' (mydatapi.aade.gr/myDATA). Any other value is refused and nothing is issued. Dev and prod are separate portals with separate credentials.",
    },
    {
        "name": "MYDATA_INVOICE_SERIES_PREFIX",
        "type": "string",
        "value": "GRVP",
        "description": "Prefix for the AADE series code. Combined with the issuer VAT + year to form e.g. 'GRVP-2026'.",
    },
    {
        "name": "MYDATA_CREDIT_SERIES_PREFIX",
        "type": "string",
        "value": "GRVC",
        "description": "Prefix for the AADE series code of the retail credit documents (11.4), which are a gapless series of their own. Combined with the year to form e.g. 'GRVC-2026'.",
    },
    {
        "name": "MYDATA_ISSUER_BRANCH",
        "type": "int",
        "value": 0,
        "description": "AADE branch code — 0 for main branch. Change only if invoicing from a registered secondary establishment.",
    },
    {
        "name": "MYDATA_REQUEST_TIMEOUT_SECONDS",
        "type": "int",
        "value": 30,
        "description": "HTTP request timeout in seconds. AADE endpoints are sometimes slow under load — 30s is the usual sweet spot.",
    },
    {
        "name": "ANNOUNCEMENT_BAR",
        "type": "json",
        "value": {},
        "validator": "tenant.validators.validate_announcement_bar_setting",
        "description": (
            "Strip above the storefront header, as JSON: "
            '{"enabled": bool, "text": "<default-locale wording>", '
            '"shortText"?: "<phone wording>", "code"?: "<promo code>", '
            '"i18n": {"<locale>": {"text"?: "...", "shortText"?: '
            '"..."}}, "link"?, "icon"?, "color"?, "dismissible"?, '
            '"id"?}. `text` is capped at 200 characters because the '
            "bar is one line, `shortText` at 80; `code` is one "
            "whitespace-free token of at most 40 characters, shown set "
            "apart from the sentence and the same in every language "
            "(so not an i18n key); `color` is one of "
            "primary/secondary/neutral/info/success/warning/error. "
            "`id` is what a dismissal is remembered against in the "
            "visitor's browser — change it to re-show the bar to "
            "everyone. Empty {} = no bar."
        ),
    },
    {
        "name": "AUTH_PANEL",
        "type": "json",
        "value": {},
        "validator": "tenant.validators.validate_auth_panel_setting",
        "description": (
            "Photo and line on the ink panel beside the sign-in pages, "
            'as JSON: {"imageUrl"?: "<stored media path>", "tagline"?: '
            '"<default-locale wording>", "i18n": {"<locale>": '
            '{"tagline": "..."}}}. `tagline` is capped at 200 '
            "characters. Both keys are optional; without them the panel "
            "is the store's logo on ink. Empty {} = nothing configured."
        ),
    },
    {
        "name": "FUNDING_BANNER",
        "type": "json",
        "value": {"enabled": False},
        "validator": "tenant.validators.validate_funding_banner_setting",
        "description": (
            "EU/ESPA co-funding strip at the very top of every "
            'storefront page, as JSON: {"enabled": bool, "image": '
            '"<logo strip: /path or https URL>", "width": <intrinsic '
            'width in pixels>, "height": <intrinsic height in pixels>, '
            '"alt": "<accessible name of the logos>", "link": "<page '
            "describing the funded "
            'operation: /path or https URL>", "label"?: "<visible link '
            'text>"}. `image`, `alt` and `link` are required when '
            "enabled, and `width` and `height` with `image`: the "
            "storefront reserves the artwork's box at that ratio so the "
            "strip does not push the page down as it loads. `image` "
            "and `link` are at most 1000 characters, "
            "`alt` 300, `label` 120. Links must be site-relative paths "
            "or https URLs. A disabled banner is just "
            '{"enabled": false}. `i18n` overrides it per language: '
            '{"en": {"image", "width", "height", "alt", "label"}}, any '
            "of them, the artwork always with its own size; the link is "
            "shared and the storefront adds the locale prefix."
        ),
    },
    {
        "name": "DEMO_ACCOUNT_ENABLED",
        "type": "bool",
        "value": False,
        "description": (
            "Show a demo-account card on the login page with the two "
            "values below, so a prospect can see the logged-in "
            "storefront without signing up. INTENDED FOR A DEMO STORE "
            "ONLY: turning it on publishes DEMO_ACCOUNT_EMAIL and "
            "DEMO_ACCOUNT_PASSWORD to every visitor through "
            "/api/v1/settings/public. The seeder writes all three only "
            "on a tenant flagged is_demo."
        ),
    },
    {
        "name": "DEMO_ACCOUNT_EMAIL",
        "type": "string",
        "value": "",
        "description": (
            "Email of the shared demo account. PUBLIC when "
            "DEMO_ACCOUNT_ENABLED is on. Empty → the card does not "
            "render, whatever the flag says."
        ),
    },
    {
        "name": "DEMO_ACCOUNT_PASSWORD",
        "type": "string",
        "value": "",
        "description": (
            "Password of the shared demo account, shown IN CLEAR on the "
            "login page when DEMO_ACCOUNT_ENABLED is on. Only ever a "
            "throwaway account on a demo store — one whose data a "
            "nightly task resets. Never a real customer's, and never a "
            "staff account."
        ),
    },
    {
        "name": "DEMO_ACCOUNT_B2B_EMAIL",
        "type": "string",
        "value": "",
        "description": (
            "Optional SECOND demo login, for the wholesale experience. "
            "Kept separate from DEMO_ACCOUNT_EMAIL because a B2B "
            "account changes every price on the storefront: a prospect "
            "shown wholesale numbers would read them as the retail "
            "ones. PUBLIC when DEMO_ACCOUNT_ENABLED is on. Empty → the "
            "login card shows only the retail account."
        ),
    },
    {
        "name": "DEMO_ACCOUNT_B2B_PASSWORD",
        "type": "string",
        "value": "",
        "description": (
            "Password of the wholesale demo account, shown IN CLEAR "
            "beside DEMO_ACCOUNT_B2B_EMAIL. Same rule as the retail "
            "one: only ever a throwaway account on a demo store."
        ),
    },
]
