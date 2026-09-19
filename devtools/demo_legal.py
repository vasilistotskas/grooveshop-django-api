"""English legal documents for the demo store.

`Tenant` refuses to serve a locale whose legal documents have no body in
it, because the storefront 404s them — which is the one thing terms, a
privacy policy and a cookie policy exist to prevent. So a demo store
that serves `en` needs these before `available_locales` can include it.

These mirror `page_config/legal_documents.py` — the PLATFORM's own Greek
boilerplate, which every tenant is seeded with at provisioning — section
for section, including the `id` on each `<section>`, because the legal
route builds its table of contents from those ids and an anchor that
resolves to nothing is a link to the top of the page.

Scoped to the demo store rather than added to the platform module on
purpose: the platform's English legal copy is a decision for whoever
owns the wording, and seeding it here changes what ONE disposable
showcase serves rather than what every tenant is provisioned with.

Two substitutions, the same two the platform module takes, resolved
once at seed time against the tenant being seeded.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

TERMS = """
<section id="scope"><h2>Scope</h2>
<p>Using {site_host} means you accept the terms below. They govern the
relationship between you and {store_name} for everything on this
site.</p>
<p>{store_name} is a DEMONSTRATION store. Nothing here is offered for
sale, no order placed on it will ever be dispatched, and no payment is
taken. It exists so that people evaluating the platform can see how a
real shop behaves.</p></section>

<section id="acceptance"><h2>Accepting these terms</h2>
<p>If you do not accept these terms, please stop using the site. Using
it — browsing, searching, creating an account, adding to a basket —
counts as acceptance.</p></section>

<section id="modifications"><h2>Changes to these terms</h2>
<p>We may change these terms at any time. The version published on this
page is the one in force, and continuing to use the site after a change
means you accept the new version. Check this page from time to
time.</p></section>

<section id="applicable-law"><h2>Governing law</h2>
<p>These terms are governed by Greek law and by the European Union law
that applies in Greece, including the legislation on distance selling
and on the protection of personal data.</p></section>

<section id="jurisdiction"><h2>Disputes</h2>
<p>We would rather settle any disagreement directly, so please contact
us first. Where that is not possible, the courts of Thessaloniki have
jurisdiction. Consumers may also use the European Commission's online
dispute resolution platform.</p></section>

<section id="user-obligations"><h2>Your obligations</h2>
<p>You agree to give accurate details, to keep your account credentials
to yourself, and not to use the site in a way that damages it, its
other users, or anybody's rights. You are responsible for what happens
under your account.</p>
<p>On this demonstration store the sign-in details are published openly
on the login page and shared by everybody who visits, so please do not
put anything into it that you would not want a stranger to read.</p>
</section>
""".strip()

PRIVACY = """
<section id="intro"><h2>Introduction</h2>
<p>This policy explains what {store_name} does with personal data
collected through {site_host}, and the rights you have over it. We
process personal data in line with the General Data Protection
Regulation (EU 2016/679) and Greek data protection law.</p>
<p>{store_name} is a demonstration store. The accounts and orders on it
are fixtures, and the shared demo account is reset every night — so
anything entered into it is deleted rather than kept.</p></section>

<section id="data-categories"><h2>What categories of personal data we
process</h2>
<p>Identification and contact details (name, email address, telephone
number), delivery and billing addresses, order history, and technical
data your browser sends such as IP address, device type and the pages
you viewed.</p>
<p>We do not process special categories of personal data, and we do not
take payment card details: where a payment provider is used, the card
details go to that provider and never reach us.</p></section>

<section id="account-creation"><h2>To create an account on
{site_host}</h2>
<p>When you register we process your email address, your name and your
password — stored only as a cryptographic hash, never in a form anybody
can read. The legal basis is the performance of a contract: without
these details there is no account.</p>
<p>You may delete your account at any time from your account settings,
which removes the personal data we hold for it except where we are
required by law to keep a record.</p></section>

<section id="marketing-communications"><h2>To tell you about news and
offers</h2>
<p>If you subscribe to the newsletter we process your email address to
send it. The legal basis is your consent, which you may withdraw at any
time — every message carries an unsubscribe link, and withdrawing does
not affect anything sent before.</p>
<p>You have the right to access, rectify, erase, restrict and port your
personal data, and to object to its processing. To exercise any of
these, or to complain, contact us through the contact page; you may
also complain to the Hellenic Data Protection Authority.</p></section>
""".strip()

COOKIES = """
<section id="intro"><h2>Introduction</h2>
<p>{site_host} uses cookies and similar technologies. This policy
explains which ones, what they do, and how to control them. It sits
alongside our privacy policy.</p></section>

<section id="what-are-cookies"><h2>What cookies are</h2>
<p>A cookie is a small text file a site asks your browser to store, and
which the browser sends back on later visits. It lets a site recognise
your browser — to keep you signed in, to remember what is in your
basket, or to count how many people read a page.</p></section>

<section id="general-classification"><h2>How cookies are classified</h2>
<p>By lifetime, a cookie is either a session cookie, deleted when you
close the browser, or a persistent cookie, which stays until it expires
or you remove it. By origin, it is either first-party, set by this site,
or third-party, set by another service used on it.</p></section>

<section id="cookies-we-use"><h2>Our site</h2>
<p>We set cookies to keep you signed in, to hold your basket between
pages, to remember your language and whether you chose the light or dark
theme, and to remember the cookie choices you made here so you are not
asked again on every page.</p></section>

<section id="functional-categories"><h2>Cookies by purpose</h2>
<p><strong>Strictly necessary</strong> — sign-in, basket, security and
the consent record itself. The site cannot work without these, so they
are not optional.</p>
<p><strong>Functionality</strong> — language, theme and similar
preferences. Refusing them only means the site forgets your choices.</p>
<p><strong>Analytics</strong> — how many people visit and which pages
they read, so we can improve them. Set only with your consent.</p>
<p><strong>Advertising and personalisation</strong> — measuring
campaigns and tailoring what you are shown. Set only with your
consent.</p></section>

<section id="how-to-control"><h2>How to control cookies</h2>
<p>Use the cookie settings link in the footer to change your choices at
any time, including withdrawing consent you gave earlier.</p>
<p>Every browser also lets you block or delete cookies in its own
settings. Blocking the strictly necessary ones will stop parts of the
site working — you will not be able to stay signed in or keep a
basket.</p></section>
""".strip()

DOCUMENTS: dict[str, dict[str, str]] = {
    "terms": {"title": "Terms of Use", "body": TERMS},
    "privacy": {"title": "Privacy Policy", "body": PRIVACY},
    "cookies": {"title": "Cookie Policy", "body": COOKIES},
}


def render(slug: str, *, site_host: str, store_name: str) -> str:
    """Substitute the tenant into the document.

    ``str.replace`` rather than ``str.format``, for the same reason the
    platform module gives: these are hand-authored HTML and a stray
    brace in future wording would make ``format`` raise.
    """
    body = DOCUMENTS[slug]["body"]
    return body.replace("{site_host}", site_host).replace(
        "{store_name}", store_name
    )


def seed_english_legal_documents() -> dict[str, int]:
    """Give the three legal documents an English body.

    Written only where English is absent or empty: a merchant who has
    translated their own keeps it, and a re-run is a no-op. An empty row
    counts as absent, because the legal route 404s on an empty body and
    parler will not fall back past a row that exists.

    Call inside the tenant's ``schema_context``.
    """
    from page_config.defaults import tenant_document_context
    from page_config.models import ContentPage

    report: dict[str, int] = {}

    def bump(key: str) -> None:
        report[key] = report.get(key, 0) + 1

    site_host, store_name = tenant_document_context()

    for slug, document in DOCUMENTS.items():
        page = ContentPage.objects.filter(slug=slug).first()
        if page is None:
            logger.warning(
                "Legal document %s is missing from this schema", slug
            )
            bump("missing")
            continue

        translation = page.translations.filter(language_code="en").first()
        if translation is not None and (translation.body or "").strip():
            bump("kept")
            continue

        page.set_current_language("en")
        page.title = document["title"]
        page.body = render(slug, site_host=site_host, store_name=store_name)
        page.save()
        bump("written")

    return report
