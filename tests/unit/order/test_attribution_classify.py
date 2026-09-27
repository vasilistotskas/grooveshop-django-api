"""``order.attribution.classify`` — which signal names an order's source.

A table of one visit per row, so the precedence order (agent, UTM,
click id, known referrer, in-app User-Agent, other referrer, direct) is
readable in one place, plus the privacy rules the module promises: the
referrer is reduced to its host, the store's own domains are never a
source, and the landing path loses its query string.
"""

from __future__ import annotations

import pytest

from order.attribution import (
    CAMPAIGN_MAX_LENGTH,
    SOURCE_MAX_LENGTH,
    AttributionInput,
    classify,
    source_label,
)
from order.enum.attribution import OrderSourceType

OWN_HOSTS = frozenset({"webside.gr", "api.webside.gr"})

INSTAGRAM_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 "
    "Instagram 330.0.0.40.92 (iPhone14,5; iOS 17_4; el_GR; el)"
)
FACEBOOK_UA = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Version/4.0 Chrome/124.0 Mobile Safari/537.36 "
    "[FB_IAB/FB4A;FBAV/460.0.0.48.109;]"
)
TIKTOK_UA = (
    "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 (KHTML, like "
    "Gecko) Chrome/120.0 Mobile Safari/537.36 musical_ly_2023405030 "
    "BytedanceWebview/d8a21c6"
)
SAFARI_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 "
    "Mobile/15E148 Safari/604.1"
)


@pytest.mark.parametrize(
    ("inp", "source_type", "source"),
    [
        pytest.param(
            AttributionInput(
                agent_protocol="ucp",
                utm_source="google",
                click_ids=("gclid",),
            ),
            OrderSourceType.AGENT,
            "ucp",
            id="agent-protocol-beats-everything",
        ),
        pytest.param(
            AttributionInput(utm_source="ig", utm_medium="social"),
            OrderSourceType.CAMPAIGN,
            "instagram",
            id="utm-alias-normalised",
        ),
        pytest.param(
            AttributionInput(utm_source="Newsletter", utm_medium="email"),
            OrderSourceType.CAMPAIGN,
            "newsletter",
            id="unknown-utm-source-lowercased",
        ),
        pytest.param(
            AttributionInput(utm_source="chatgpt.com"),
            OrderSourceType.CAMPAIGN,
            "chatgpt",
            id="utm-source-given-as-a-domain",
        ),
        pytest.param(
            AttributionInput(utm_source="facebook", utm_medium="cpc"),
            OrderSourceType.PAID,
            "facebook",
            id="utm-paid-medium",
        ),
        pytest.param(
            AttributionInput(utm_source="google", click_ids=("gclid",)),
            OrderSourceType.PAID,
            "google",
            id="utm-with-paid-click-id-is-paid",
        ),
        pytest.param(
            AttributionInput(
                utm_source="newsletter",
                referrer="https://www.google.com/",
                user_agent=INSTAGRAM_UA,
            ),
            OrderSourceType.CAMPAIGN,
            "newsletter",
            id="utm-beats-referrer-and-user-agent",
        ),
        pytest.param(
            AttributionInput(
                click_ids=("gbraid",), referrer="https://www.bing.com/"
            ),
            OrderSourceType.PAID,
            "google",
            id="click-id-beats-referrer",
        ),
        pytest.param(
            AttributionInput(click_ids=("msclkid",)),
            OrderSourceType.PAID,
            "bing",
            id="microsoft-ads-click-id",
        ),
        pytest.param(
            AttributionInput(click_ids=("fbclid", "ttclid")),
            OrderSourceType.PAID,
            "tiktok",
            id="paid-click-id-before-fbclid",
        ),
        pytest.param(
            AttributionInput(click_ids=("fbclid",)),
            OrderSourceType.SOCIAL,
            "facebook",
            id="fbclid-is-social",
        ),
        pytest.param(
            AttributionInput(click_ids=("fbclid",), user_agent=INSTAGRAM_UA),
            OrderSourceType.SOCIAL,
            "instagram",
            id="fbclid-in-instagram-app",
        ),
        pytest.param(
            AttributionInput(click_ids=("ttclid",), user_agent=INSTAGRAM_UA),
            OrderSourceType.PAID,
            "tiktok",
            id="user-agent-does-not-rename-an-unrelated-click-id",
        ),
        pytest.param(
            AttributionInput(referrer="https://l.instagram.com/?u=x"),
            OrderSourceType.SOCIAL,
            "instagram",
            id="instagram-link-shim",
        ),
        pytest.param(
            AttributionInput(referrer="https://lm.facebook.com/"),
            OrderSourceType.SOCIAL,
            "facebook",
            id="facebook-mobile-link-shim",
        ),
        pytest.param(
            AttributionInput(referrer="https://t.co/abc"),
            OrderSourceType.SOCIAL,
            "x",
            id="x-link-wrapper",
        ),
        pytest.param(
            AttributionInput(referrer="https://www.google.gr/"),
            OrderSourceType.SEARCH,
            "google",
            id="google-greek-search",
        ),
        pytest.param(
            AttributionInput(referrer="https://gr.search.yahoo.com/"),
            OrderSourceType.SEARCH,
            "yahoo",
            id="yahoo-country-search",
        ),
        pytest.param(
            AttributionInput(referrer="https://www.perplexity.ai/search"),
            OrderSourceType.REFERRAL,
            "perplexity",
            id="answer-engine-referrer",
        ),
        pytest.param(
            AttributionInput(
                referrer="https://www.google.com/", user_agent=TIKTOK_UA
            ),
            OrderSourceType.SEARCH,
            "google",
            id="known-referrer-beats-user-agent",
        ),
        pytest.param(
            AttributionInput(user_agent=INSTAGRAM_UA),
            OrderSourceType.SOCIAL,
            "instagram",
            id="instagram-in-app-browser",
        ),
        pytest.param(
            AttributionInput(user_agent=FACEBOOK_UA),
            OrderSourceType.SOCIAL,
            "facebook",
            id="facebook-in-app-browser",
        ),
        pytest.param(
            AttributionInput(user_agent=TIKTOK_UA),
            OrderSourceType.SOCIAL,
            "tiktok",
            id="tiktok-in-app-browser",
        ),
        pytest.param(
            AttributionInput(
                referrer="https://www.blog.example.org/post?id=7",
                user_agent=INSTAGRAM_UA,
            ),
            OrderSourceType.SOCIAL,
            "instagram",
            id="user-agent-beats-unknown-referrer",
        ),
        pytest.param(
            AttributionInput(referrer="https://www.blog.example.org/post"),
            OrderSourceType.REFERRAL,
            "blog.example.org",
            id="other-referrer-is-referral",
        ),
        pytest.param(
            AttributionInput(referrer="https://www.webside.gr/products"),
            OrderSourceType.DIRECT,
            "",
            id="own-host-referrer-ignored",
        ),
        pytest.param(
            AttributionInput(
                referrer="android-app://com.google.android.gm/",
                user_agent=SAFARI_UA,
            ),
            OrderSourceType.DIRECT,
            "",
            id="non-http-referrer-ignored",
        ),
        pytest.param(
            AttributionInput(referrer="http://[::1"),
            OrderSourceType.DIRECT,
            "",
            id="unparseable-referrer-ignored",
        ),
        pytest.param(
            AttributionInput(utm_medium="email", user_agent=SAFARI_UA),
            OrderSourceType.DIRECT,
            "",
            id="medium-without-source-is-not-a-campaign",
        ),
        pytest.param(
            AttributionInput(),
            OrderSourceType.DIRECT,
            "",
            id="no-signal-is-direct",
        ),
    ],
)
def test_classify(inp, source_type, source):
    result = classify(inp, own_hosts=OWN_HOSTS)

    assert (result.source_type, result.source) == (source_type, source)


class TestWhatIsKept:
    def test_only_the_referrer_host_is_kept(self):
        result = classify(
            AttributionInput(
                referrer="https://www.blog.example.org/p?email=a@b.gr"
            ),
            own_hosts=OWN_HOSTS,
        )

        assert result.referrer_host == "blog.example.org"

    def test_an_own_host_referrer_is_not_kept_either(self):
        result = classify(
            AttributionInput(
                utm_source="ig", referrer="https://api.webside.gr/x"
            ),
            own_hosts=OWN_HOSTS,
        )

        assert result.referrer_host == ""
        assert result.source == "instagram"

    def test_the_landing_path_loses_its_query_string(self):
        result = classify(
            AttributionInput(landing_path="/products/7?gclid=Cj0KCQ&x=1"),
            own_hosts=OWN_HOSTS,
        )

        assert result.landing_path == "/products/7"

    def test_a_landing_path_that_is_not_a_path_is_dropped(self):
        result = classify(
            AttributionInput(landing_path="javascript:alert(1)"),
            own_hosts=OWN_HOSTS,
        )

        assert result.landing_path == ""

    def test_medium_and_campaign_are_kept_and_capped(self):
        result = classify(
            AttributionInput(
                utm_source="x" * 500,
                utm_medium="Social",
                utm_campaign="C" * 500,
            ),
            own_hosts=OWN_HOSTS,
        )

        assert result.medium == "social"
        assert result.campaign == "C" * CAMPAIGN_MAX_LENGTH
        assert len(result.source) == SOURCE_MAX_LENGTH


class TestFromPayload:
    def test_reads_the_validated_payload_and_the_user_agent(self):
        inp = AttributionInput.from_payload(
            {
                "utm_source": " ig ",
                "click_ids": ["fbclid"],
                "referrer": "https://l.instagram.com/",
                "landing_path": "/",
                "agent_protocol": "acp",
            },
            user_agent=INSTAGRAM_UA,
            from_agent_gateway=True,
        )

        assert inp == AttributionInput(
            utm_source="ig",
            click_ids=("fbclid",),
            referrer="https://l.instagram.com/",
            landing_path="/",
            agent_protocol="acp",
            user_agent=INSTAGRAM_UA,
        )

    def test_an_agent_protocol_without_the_gateway_proof_is_dropped(self):
        inp = AttributionInput.from_payload(
            {"utm_source": "ig", "agent_protocol": "acp"},
            user_agent="",
            from_agent_gateway=False,
        )

        assert inp == AttributionInput(utm_source="ig")

    def test_no_payload_is_an_empty_input(self):
        assert (
            AttributionInput.from_payload(
                None, user_agent="", from_agent_gateway=False
            )
            == AttributionInput()
        )


class TestSourceLabel:
    def test_a_registry_key_reads_as_its_brand(self):
        assert source_label("instagram", OrderSourceType.SOCIAL) == (
            "Instagram"
        )

    def test_an_agent_protocol_names_the_agent(self):
        assert "UCP" in str(source_label("ucp", OrderSourceType.AGENT))

    def test_a_direct_visit_reads_as_its_type(self):
        assert source_label("", OrderSourceType.DIRECT) == (
            OrderSourceType.DIRECT.label
        )

    def test_a_referral_host_is_shown_as_is(self):
        assert source_label("blog.example.org", OrderSourceType.REFERRAL) == (
            "blog.example.org"
        )
