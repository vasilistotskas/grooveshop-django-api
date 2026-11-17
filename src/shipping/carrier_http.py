"""The HTTP session every carrier client sends through.

``shipping_acs.client.AcsClient`` and ``shipping_boxnow.client.BoxNowClient``
build their ``requests`` session with :func:`carrier_session`, so both
follow one rule for what the HTTP layer may send a second time:

- a request that never connected is sent again, whatever its method:
  nothing reached the courier;
- a GET is also sent again after a 5xx or a lost answer (read timeout,
  connection reset): it changes nothing at the courier;
- a POST or PUT is never sent again once it may have reached the
  courier. Neither courier can tell a re-sent write from a new one (ACS's
  ``ACS_Create_Voucher`` has no idempotency key, BoxNow answers a second
  ``orderNumber`` with P410), so the 5xx or the lost answer goes to the
  client, which raises its own error and lets the caller decide.

urllib3 sorts an error by when it happened. A connect error (a connect
timeout or a refused connection) means the request never reached the
courier and is retried for any method. A read error (a read timeout or a
dropped connection) means it may have, so ``allowed_methods`` decides
and a POST or PUT is never sent again. Every other error (a TLS failure,
say) is retried for any method unless ``other`` is 0, because urllib3
does not check the method for it: ``other=0`` stops it, and a GET
therefore gets no retry after such an error either. A 5xx is retried only
for a method in ``allowed_methods``. Back-off between attempts: 0 s,
1 s, 2 s.
"""

from __future__ import annotations

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

CARRIER_RETRY = Retry(
    total=3,
    other=0,
    backoff_factor=0.5,
    status_forcelist=[500, 502, 503, 504],
    allowed_methods=["GET"],
    raise_on_status=False,  # the clients inspect the status themselves
)


def carrier_session() -> requests.Session:
    """A ``requests`` session whose every request follows
    :data:`CARRIER_RETRY`."""
    session = requests.Session()
    adapter = HTTPAdapter(max_retries=CARRIER_RETRY)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session
