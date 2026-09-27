"""Where an order came from: the known-source registry and the classifier.

Pure domain code — no ORM, no request. ``OrderAttributionService`` feeds
it the storefront's first-touch capture plus the request's User-Agent
and stores the result on ``OrderAttribution``.

Every input is shopper-controlled (a UTM can be typed by hand), so the
result is analytics, never authorisation. Only the referrer's HOST is
kept, never its path or query, and click ids arrive as parameter NAMES:
their values identify a person to an ad network and are never sent.

Precedence, first match wins — WooCommerce's order-attribution model
(https://woocommerce.com/document/order-attribution-tracking/) plus the
two signals an in-app browser still leaves when it strips ``Referer``:

1. agent protocol (the agent gateway)
2. ``utm_source``
3. ad click id
4. known referrer host
5. in-app browser User-Agent marker
6. any other external referrer
7. direct

Registry sources. Referrer hosts and ``utm_source`` aliases are checked
against Google Analytics 4's default channel group source list
(https://support.google.com/analytics/answer/9756891); the entries that
list does not carry are cited where they are declared.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from django.utils.translation import gettext_lazy as _
from django_stubs_ext import StrOrPromise

from order.enum.attribution import OrderSourceType

SOURCE_MAX_LENGTH = 100
MEDIUM_MAX_LENGTH = 100
CAMPAIGN_MAX_LENGTH = 200
REFERRER_HOST_MAX_LENGTH = 255
LANDING_PATH_MAX_LENGTH = 500

# GA4's own "Paid Search" / "Paid Social" medium rule, verbatim.
_PAID_MEDIUM = re.compile(r"^(.*cp.*|ppc|retargeting|paid.*)$")


@dataclass(frozen=True, slots=True)
class KnownSource:
    """A channel the classifier names instead of reporting a bare host.

    ``referrer_domains`` match the host itself or any subdomain, so
    ``facebook.com`` covers the ``l.``/``lm.``/``m.`` link shims.
    ``user_agent_markers`` are case-sensitive substrings an in-app
    browser adds to its User-Agent.
    """

    key: str
    label: str
    source_type: OrderSourceType
    referrer_domains: tuple[str, ...] = ()
    utm_aliases: tuple[str, ...] = ()
    user_agent_markers: tuple[str, ...] = ()


# Order matters only for User-Agent markers: Instagram is checked before
# Facebook so a Meta app that carried both tokens reads as Instagram.
KNOWN_SOURCES: tuple[KnownSource, ...] = (
    KnownSource(
        # google.gr is Google's Greek ccTLD, the home market's search.
        "google",
        "Google",
        OrderSourceType.SEARCH,
        referrer_domains=("google.com", "google.gr"),
        utm_aliases=("google",),
    ),
    KnownSource(
        "bing",
        "Bing",
        OrderSourceType.SEARCH,
        referrer_domains=("bing.com",),
        utm_aliases=("bing",),
    ),
    KnownSource(
        # DuckDuckGo sends its origin as the referrer
        # (https://duckduckgo.com/duckduckgo-help-pages/results/rduckduckgocom).
        "duckduckgo",
        "DuckDuckGo",
        OrderSourceType.SEARCH,
        referrer_domains=("duckduckgo.com",),
        utm_aliases=("duckduckgo",),
    ),
    KnownSource(
        # ``search.`` only: GA4 files answers./bookmarks.yahoo.com
        # under social, not search.
        "yahoo",
        "Yahoo",
        OrderSourceType.SEARCH,
        referrer_domains=("search.yahoo.com",),
        utm_aliases=("yahoo",),
    ),
    KnownSource(
        # Instagram appends "Instagram <version>" to its WebView UA.
        "instagram",
        "Instagram",
        OrderSourceType.SOCIAL,
        referrer_domains=("instagram.com",),
        utm_aliases=("instagram", "ig"),
        user_agent_markers=("Instagram",),
    ),
    KnownSource(
        # Facebook and Messenger add FBAN/FBAV on iOS and FB_IAB on
        # Android.
        "facebook",
        "Facebook",
        OrderSourceType.SOCIAL,
        referrer_domains=("facebook.com", "fb.me"),
        utm_aliases=("facebook", "fb"),
        user_agent_markers=("FBAN", "FBAV", "FB_IAB"),
    ),
    KnownSource(
        # TikTok's WebView UA carries "musical_ly" (the app's former
        # name) and "BytedanceWebview".
        "tiktok",
        "TikTok",
        OrderSourceType.SOCIAL,
        referrer_domains=("tiktok.com",),
        utm_aliases=("tiktok",),
        user_agent_markers=("musical_ly", "BytedanceWebview"),
    ),
    KnownSource(
        # The app's UA ends "[Pinterest/iOS]" or "[Pinterest/Android]".
        "pinterest",
        "Pinterest",
        OrderSourceType.SOCIAL,
        referrer_domains=("pinterest.com",),
        utm_aliases=("pinterest",),
        user_agent_markers=("[Pinterest/",),
    ),
    KnownSource(
        "youtube",
        "YouTube",
        OrderSourceType.SOCIAL,
        referrer_domains=("youtube.com",),
        utm_aliases=("youtube",),
    ),
    KnownSource(
        # x.com is X's own domain since the rename. Only the iOS app
        # names itself ("Twitter for iPhone"); the Android one does not.
        "x",
        "X",
        OrderSourceType.SOCIAL,
        referrer_domains=("t.co", "x.com", "twitter.com"),
        utm_aliases=("x", "twitter"),
        user_agent_markers=("Twitter for iPhone",),
    ),
    KnownSource(
        # The app's WebView UA carries "[LinkedInApp]".
        "linkedin",
        "LinkedIn",
        OrderSourceType.SOCIAL,
        referrer_domains=("linkedin.com", "lnkd.in"),
        utm_aliases=("linkedin",),
        user_agent_markers=("LinkedInApp",),
    ),
    KnownSource(
        "reddit",
        "Reddit",
        OrderSourceType.SOCIAL,
        referrer_domains=("reddit.com",),
        utm_aliases=("reddit",),
    ),
    KnownSource(
        # Not in GA4's list. ChatGPT tags its links
        # ``utm_source=chatgpt.com`` (matched as a domain below), and
        # Perplexity sends perplexity.ai as the referrer.
        "chatgpt",
        "ChatGPT",
        OrderSourceType.REFERRAL,
        referrer_domains=("chatgpt.com",),
    ),
    KnownSource(
        "perplexity",
        "Perplexity",
        OrderSourceType.REFERRAL,
        referrer_domains=("perplexity.ai",),
    ),
)

_SOURCES_BY_KEY: dict[str, KnownSource] = {s.key: s for s in KNOWN_SOURCES}
_SOURCES_BY_ALIAS: dict[str, KnownSource] = {
    alias: s for s in KNOWN_SOURCES for alias in s.utm_aliases
}


@dataclass(frozen=True, slots=True)
class ClickIdSource:
    """What an ad click-id URL parameter says about the visit.

    ``in_app_sources`` are sibling apps that stamp the same parameter:
    when the User-Agent names one of them, it wins over ``source``.
    """

    source: str
    source_type: OrderSourceType
    in_app_sources: tuple[str, ...] = ()


# Checked in this order when a visit carries several. The paid ids come
# first; ``fbclid`` last, because Meta adds it to organic outbound links
# and shares too, not just to ad clicks — so it means "social".
#   gclid/gbraid/wbraid — Google Ads auto-tagging (gbraid/wbraid on iOS)
#   msclkid — Microsoft Advertising auto-tagging
#   ttclid — https://ads.tiktok.com/help/article/tiktok-click-id
#   twclid — https://docs.x.com/x-ads-api/measurement/web-conversions
#   li_fat_id — LinkedIn Insight Tag enhanced conversion tracking
#   epik — https://help.pinterest.com/en/business/article/pinterest-tag-parameters-and-cookies
CLICK_ID_PARAMS: dict[str, ClickIdSource] = {
    "gclid": ClickIdSource("google", OrderSourceType.PAID),
    "gbraid": ClickIdSource("google", OrderSourceType.PAID),
    "wbraid": ClickIdSource("google", OrderSourceType.PAID),
    "msclkid": ClickIdSource("bing", OrderSourceType.PAID),
    "ttclid": ClickIdSource("tiktok", OrderSourceType.PAID),
    "twclid": ClickIdSource("x", OrderSourceType.PAID),
    "li_fat_id": ClickIdSource("linkedin", OrderSourceType.PAID),
    "epik": ClickIdSource("pinterest", OrderSourceType.PAID),
    "fbclid": ClickIdSource(
        "facebook", OrderSourceType.SOCIAL, in_app_sources=("instagram",)
    ),
}

# The protocols the agent gateway speaks: Universal Commerce Protocol
# and Agentic Commerce Protocol.
AGENT_PROTOCOLS: dict[str, str] = {"ucp": "UCP", "acp": "ACP"}


@dataclass(frozen=True, slots=True)
class AttributionInput:
    """The raw signals, trimmed but not yet interpreted."""

    utm_source: str = ""
    utm_medium: str = ""
    utm_campaign: str = ""
    click_ids: tuple[str, ...] = ()
    referrer: str = ""
    landing_path: str = ""
    agent_protocol: str = ""
    user_agent: str = ""

    @classmethod
    def from_payload(
        cls, payload: Mapping[str, Any] | None, *, user_agent: str
    ) -> AttributionInput:
        """Build from ``OrderAttributionInputSerializer.validated_data``."""
        data = payload or {}
        return cls(
            utm_source=str(data.get("utm_source") or "").strip(),
            utm_medium=str(data.get("utm_medium") or "").strip(),
            utm_campaign=str(data.get("utm_campaign") or "").strip(),
            click_ids=tuple(data.get("click_ids") or ()),
            referrer=str(data.get("referrer") or "").strip(),
            landing_path=str(data.get("landing_path") or "").strip(),
            agent_protocol=str(data.get("agent_protocol") or ""),
            user_agent=user_agent,
        )


@dataclass(frozen=True, slots=True)
class ClassifiedAttribution:
    """What ``OrderAttribution`` stores, already capped to its columns."""

    source_type: OrderSourceType
    source: str = ""
    medium: str = ""
    campaign: str = ""
    referrer_host: str = ""
    landing_path: str = ""


def classify(
    inp: AttributionInput, *, own_hosts: Collection[str]
) -> ClassifiedAttribution:
    """Name the channel behind an order. See the module docstring."""
    referrer_host = _external_referrer_host(inp.referrer, own_hosts)
    medium = inp.utm_medium.lower()[:MEDIUM_MAX_LENGTH]
    common = {
        "medium": medium,
        "campaign": inp.utm_campaign[:CAMPAIGN_MAX_LENGTH],
        "referrer_host": referrer_host[:REFERRER_HOST_MAX_LENGTH],
        "landing_path": _landing_path(inp.landing_path),
    }
    click_id = next(
        (CLICK_ID_PARAMS[p] for p in CLICK_ID_PARAMS if p in inp.click_ids),
        None,
    )

    if inp.agent_protocol in AGENT_PROTOCOLS:
        return ClassifiedAttribution(
            OrderSourceType.AGENT, source=inp.agent_protocol, **common
        )

    if inp.utm_source:
        raw = inp.utm_source.lower()
        known = _SOURCES_BY_ALIAS.get(raw) or _source_for_host(raw)
        # A paid click id outranks a medium that forgot to say "cpc":
        # those ids are only ever stamped on ad clicks.
        paid = bool(_PAID_MEDIUM.match(medium)) or (
            click_id is not None
            and click_id.source_type == OrderSourceType.PAID
        )
        return ClassifiedAttribution(
            OrderSourceType.PAID if paid else OrderSourceType.CAMPAIGN,
            source=known.key if known else raw[:SOURCE_MAX_LENGTH],
            **common,
        )

    in_app = _source_for_user_agent(inp.user_agent)

    if click_id is not None:
        source = click_id.source
        if in_app is not None and in_app.key in click_id.in_app_sources:
            source = in_app.key
        return ClassifiedAttribution(
            click_id.source_type, source=source, **common
        )

    if referrer_host and (known := _source_for_host(referrer_host)):
        return ClassifiedAttribution(
            known.source_type, source=known.key, **common
        )

    if in_app is not None:
        return ClassifiedAttribution(
            in_app.source_type, source=in_app.key, **common
        )

    if referrer_host:
        return ClassifiedAttribution(
            OrderSourceType.REFERRAL,
            source=referrer_host[:SOURCE_MAX_LENGTH],
            **common,
        )

    return ClassifiedAttribution(OrderSourceType.DIRECT, **common)


def source_label(source: str, source_type: str) -> StrOrPromise:
    """The human name for a stored ``(source, source_type)`` pair.

    A registry key reads as its brand, an agent protocol as the agent,
    an empty source (a direct visit) as its type; anything else — a
    referral host, an unrecognised ``utm_source`` — is already readable.
    """
    if known := _SOURCES_BY_KEY.get(source):
        return known.label
    if source_type == OrderSourceType.AGENT and source in AGENT_PROTOCOLS:
        return _("AI agent (%(protocol)s)") % {
            "protocol": AGENT_PROTOCOLS[source]
        }
    if not source:
        return OrderSourceType(source_type).label
    return source


def _normalise_host(host: str) -> str:
    return host.lower().removeprefix("www.")


def _source_for_host(host: str) -> KnownSource | None:
    host = _normalise_host(host)
    for known in KNOWN_SOURCES:
        for domain in known.referrer_domains:
            if host == domain or host.endswith(f".{domain}"):
                return known
    return None


def _source_for_user_agent(user_agent: str) -> KnownSource | None:
    if not user_agent:
        return None
    for known in KNOWN_SOURCES:
        if any(marker in user_agent for marker in known.user_agent_markers):
            return known
    return None


def _external_referrer_host(referrer: str, own_hosts: Collection[str]) -> str:
    """The referrer's host without ``www.``, or "" when there is none
    worth keeping: unparseable, not http(s), or one of the store's own
    domains (an internal navigation, or a return from checkout)."""
    if not referrer:
        return ""
    try:
        parts = urlsplit(referrer)
        host = parts.hostname or ""
    except ValueError:
        return ""
    if parts.scheme not in {"http", "https"} or not host:
        return ""
    host = _normalise_host(host)
    if host in {_normalise_host(own) for own in own_hosts}:
        return ""
    return host


def _landing_path(raw: str) -> str:
    """The path alone — the query string is where click-id values live."""
    try:
        path = urlsplit(raw).path
    except ValueError:
        return ""
    if not path.startswith("/"):
        return ""
    return path[:LANDING_PATH_MAX_LENGTH]
