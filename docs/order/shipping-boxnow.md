# Order system: BoxNow

Part of the order reference. [`docs/order/index.md`](index.md) lists every file
and says which file holds each § number used here.

## 5.2 BoxNow (`src/shipping_boxnow/`)

- **REST + webhook** for tracking events.
- Voucher mint: same 3-phase design as ACS.
- **The HTTP layer never sends a POST or PUT a second time once it may
  have reached BoxNow** (`_RETRY_CONFIG` in `src/shipping_boxnow/client.py`,
  the same rule as ACS): only a request that never connected, or a GET
  after a 5xx or a lost answer, is sent again. A 5xx or a timeout on a
  write is a `BoxNowRetryableError` and the caller decides. BoxNow refuses
  a second creation for one `orderNumber` with `P410` (manual v7.2, §3.4
  and §5), so a re-sent creation could only turn a parcel that exists into
  a reported refusal. The caller for the creation is the Celery task
  `create_boxnow_shipment_for_order`: it runs again on
  `BoxNowRetryableError`, `create_shipment_for_order` has released its
  claim, and the same `orderNumber` is sent again. A GET also gets no retry
  after an error of unknown timing (`other=0`).
- **A `P410` on creation takes over the parcel BoxNow already holds**
  (`BoxNowService._take_over_existing_parcel`, #606). The first request of
  a creation whose answer was lost (a read timeout, a 5xx, a worker that
  died before phase 3) did create the parcel, so the retry's `P410` is
  answered by looking the order up: `GET /api/v1/parcels?orderNumber=`
  ("Return only parcels related to this order number", manual v7.2,
  §6.5.3, p. 45; `BoxNowClient.get_parcel_info(order_number=...)`; never
  `q`, which is P417 for a server account, §5, p. 22). The lookup asks
  once per payment mode, because `paymentMode` has "Default value:
  prepaid" (§6.5.3; the OpenAPI file's `PaymentMode`), and reads every
  page through `pagination.next` ("Token to use to get to the next
  page"). Exactly one parcel that is not `canceled`, has no cancel
  requested (`cancelationRequested`) and is `new` is taken over, and only
  when it was created for this shipment's terms: the same locker
  (`destination.locationId`), `paymentMode` and, for `cod`,
  `amountToBeCollected`. Staff may change the locker and dispatch again,
  and BoxNow's parcel still goes to the old one. Its id is saved as
  `parcel_id`, the state `new`, the order gets the tracking number, and
  `metadata["adopted_parcel"]` records it (id and state only: the row also
  carries the recipient's contact details, which stay out of
  `metadata`). The documented row has no delivery-request id
  (the OpenAPI file's `DeliveryRequest` schema has none), so
  `delivery_request_id` stays empty and phase 1 treats a `parcel_id` as
  "already created". No such parcel (none listed, only canceled ones, one
  past `new`, more than one live, one whose cancel was requested, one
  created for other terms) is the usual `P410` refusal and creation alert,
  its message naming what the lookup found. A lookup that fails is
  that failure: a `BoxNowRetryableError` retries the task, a refusal or an
  answer with no parcel list alerts. A row's `state` is read through
  `BoxNowParcelState.from_webhook_event`, which takes both spellings the
  sources give (the OpenAPI file's `canceled`, the manual's `cancelled`).
- **A parcel created for a shipment cancelled meanwhile is cancelled at
  BoxNow** (`BoxNowService._persist_parcel`). A cancel made once the
  claim's TTL ran out can land while BoxNow is still answering the
  creation. Phase 3 then keeps the row CANCELED (no `parcel_id`, no
  tracking number), records the parcel under
  `metadata["minted_after_cancel"]`, releases the lock and cancels it at
  BoxNow by id (`_cancel_parcel_minted_after_cancel`). The entry gets
  `canceled_at_boxnow`, or `cancel_error` when BoxNow refuses or does not
  answer; in that case the parcel is also recorded under
  `metadata["orphan_delivery_requests"]` and an ERROR is logged, for ops
  to reconcile. The same holds for a parcel taken over after `P410`.
  While an entry has no outcome and is younger than
  `_MINT_CLAIM_TTL_SECONDS`, "Create BoxNow parcel now" refuses to reset
  the row (`_cancel_at_boxnow_in_flight`): the new creation would answer
  `P410` and take over the parcel that cancel is about to cancel.
- **Environment is per tenant.** `Tenant.box_now_live_mode` says whether
  the tenant's keys are BoxNow production keys (on) or stage keys (off,
  the default). `BoxNowClient` maps it to the platform's
  `BOXNOW_PRODUCTION_*` or `BOXNOW_STAGE_*` API and location hosts
  (settings/env; both sets are always present). BoxNow stage and
  production keys only authenticate against their own API, so a showcase
  store can run on stage keys beside a real store on production. The OAuth
  token cache key carries the environment. After flipping the flag, run
  the locker sync again: stage and production lockers are different sets.
- **A deployment named `staging` can never reach the production API.**
  Staging mirrors the production tenant rows, flag included;
  `BOXNOW_LIVE_MODE_ALLOWED` is off when `SYSTEM_ENV=staging`, and
  `BoxNowClient` raises `BoxNowConfigError` for a live tenant there
  instead of booking a real parcel. Webhook verification reads only the
  tenant's `webhook_secret` and is unaffected. Migration `tenant` 0054
  set the flag from what the platform URL did before: on the production
  deployment every tenant that has BoxNow keys is live, anywhere else
  every tenant is off.
- "Create BoxNow parcel now" (shipment detail action) re-dispatches a
  row left in `pending_creation` by a business error, once its data is
  fixed — the counterpart of ACS's "Issue ACS voucher now". On a
  `canceled` parcel it first archives the dead parcel to
  `metadata["previous_parcels"]` and resets the row
  (`BoxNowService.reset_shipment_for_reissue`), because a cancelled parcel
  keeps its `delivery_request_id` and creation returns an existing
  shipment unchanged; it refuses when the order itself is cancelled.
  BoxNow's `orderNumber` is still the order id, so BoxNow answering the
  new request with `P410` (duplicate) raises the usual creation alert: the
  lookup that follows a `P410` finds only the canceled parcel, which is
  never taken over. The manual says it will: `orderNumber` "must be unique
  for each request" (API Manual v7.2, §3.4, p. 10), and P410 is "Order
  number conflict. You are trying to create a delivery request for order
  ID that has already been created. Choose another order ID." (§5, p. 21),
  with no exception for a canceled request. Not yet tried on stage.
- **A `canceled` parcel event leaves the order alone**
  ([§2.1](state-machines.md)): the
  shipment is recorded CANCELED, and an order still PENDING / PROCESSING
  behind it needs follow-up ([§2.3](state-machines.md)) until staff re-issue
  the parcel or
  cancel the order. The event is the same whoever canceled the parcel
  (the shipment admin, an order cancel, BoxNow support at the store's
  request: "For other instances please refer to care@boxnow.gr", §3.7,
  p. 15); the webhook guide says partners "receive notifications for all
  parcel status changes and events" (p. 1), so a cancel made through the
  API is expected to arrive as one too (not yet verified on stage).
- **A shipment with no `parcel_id` is asked about before it cancels**
  (#606). A creation never sent (no `metadata["mint_started_at"]` and no
  `metadata["last_error"]`: an unpaid order, a store without credentials)
  cancels locally with no API call. One that was sent may have created a
  parcel whose answer was lost, so `cancel_shipment` first looks the
  order up by `orderNumber` (every payment mode, every page), with no row
  lock held, and then: nothing live there (none, or only `canceled` ones)
  cancels locally; a parcel whose cancel was only requested counts as
  live; exactly one live `new` parcel is cancelled at BoxNow by its id (`POST /api/v1/parcels/{id}:cancel`, the only cancel the
  manual has, §3.7 and §6.5.2) and its id is saved on the row; one live
  parcel past `new`, or several, is refused (`BoxNowAPIError`). A lookup
  that fails refuses the cancel as the error it is (a
  `BoxNowRetryableError` for no answer), never as "nothing there": a
  customer is told to try again or to contact the store, and a staff
  cancel goes on with the order and leaves the error in
  `shipment_cancel` and the order's history note
  ([§7.1](cancel.md)). A creation in flight (a claim younger than
  `_MINT_CLAIM_TTL_SECONDS`) refuses at once with a
  `BoxNowRetryableError`. The row is read again under its lock after the
  lookup: a creation that saved a parcel meanwhile sends the cancel down
  the usual path, and a claim taken meanwhile refuses.
- **One cancel rule**: `BoxNowService.can_cancel_shipment`. A shipment
  with no `parcel_id` can cancel (the paragraph above says how); a parcel
  cancels only while it is `new` (BoxNow answers P420
  otherwise); an already `canceled` one, sent to BoxNow or not, is a no-op
  that changes nothing, `cancel_requested_at` and the
  `metadata["cancellations"]` log included (BoxNow: "Canceling a canceled
  parcel has no effect", §3.7, p. 15; #599). `cancel_shipment`,
  the order's `canBeCanceled` and the shipment admin's two cancel
  actions ("Cancel parcels via BoxNow API" on the list, "Cancel parcel
  via BoxNow API" on the detail page) all read it, so the admin skips or
  refuses exactly what the service would refuse.
- "Download voucher PDFs (zip)" (shipment list action) returns the
  archive `BoxNowService.fetch_labels_zip` builds: one cached
  `fetch_label_bytes` call per parcel, a shipment with no parcel left
  out, a label that fails reported and the rest still bundled (the
  counterpart of `AcsService.fetch_labels_pdf`).
- **The compartment size is derived from the products' dimensions**
  (`src/shipping_boxnow/packing.py`). `Product.length` / `width` / `height`
  are nullable `Distance` fields (stored in metres, edited in cm, mm, m or
  inch next to the weight in the product admin; not in the API, nothing
  outside the server reads them). Limits are `COMPARTMENT_LIMITS_CM`
  (`src/shipping_boxnow/enum/compartment_size.py`), BOX NOW's published sizes
  (https://boxnow.gr/en/faq): Small H8, Medium H17, Large H36, all W45 x
  L60 cm. The FAQ also lists a Mini (H8 x W24 x L60); the partner API has
  no value for it (manual v7.2, error P406: "one of required sizes 1, 2 or 3
  (Small, Medium or Large)", and the OpenAPI spec 1.68, both under
  `boxnow.gr/media/hidden/`), so it is booked as Small. The
  20 kg weight limit stays on `ShippingRate.max_weight_grams`. The rule is
  conservative, never undersizing: each unit lies on its smallest side
  (height = smallest dimension), its other two sides must fit W45 x L60,
  and units are stacked (no side-by-side packing), so the parcel's height
  is the sum of the units' heights; the answer is the smallest size whose
  height holds the stack. Dimensions compare at millimetre precision
  (metres are floats). `create_shipment_row` stores the result on
  `BoxNowShipment.compartment_size`, which goes out as
  `items[].compartmentSize`.
  **The checkout no longer supplies a size** (the server owns it; an old
  client still sending `boxnowCompartmentSize` is ignored, not rejected).
  **Products without dimensions** get the store's
  `BOXNOW_DEFAULT_COMPARTMENT_SIZE` setting (1 = Small by default, 2 and 3
  allowed, validated). A cart with some measured and some unmeasured items
  takes the larger of the derived size and that setting; with every item
  measured the derived size wins. The free gifts a cart is entitled to
  (`PromotionEngine.gift_pairs`) are added to the cart's lines everywhere the
  parcel is weighed or sized (payment intent, order verification and creation,
  the shipment row, the options flag, the locker search), because they become
  physical order lines. A gift the engine leaves out (sold out) adds nothing;
  one lost to a later stock race only makes the booked size larger.
- **A cart that fits no compartment is refused at checkout**, next to the
  weight cap: `ShippingService.assert_available(items=...)` asks the
  adapter (`ShippingCarrierInterface.assert_parcel_fits`, no limit by
  default) and a `ShippingParcelTooLargeError` becomes the 400
  `invalid_order_data` ("The cart is too large for the selected shipping
  method.") on `shippingProviderCode`, from order creation and from the
  create-payment-intent endpoint. `/shipping/options` finds the cart the
  way every cart endpoint does (`X-Cart-Id`, or the signed-in user's
  cart; `CartService`) and flags each option the cart does not fit with
  `exceeds_parcel_size`, by the same `assert_parcel_fits` rule. The option
  is still listed. With no cart, or an unknown one, it is always false.
  The response varies by cart, so a shared cache in front of this route
  must not serve it across carts.
  `POST /shipping/boxnow/lockers/nearest` (the closest-locker lookup)
  derives its `requiredSize` the same way, from the cart at `X-Cart-Id`
  (the store default when there is none) and refuses a cart that fits no
  compartment with 400 `parcel_too_large`; the old `compartment_size` body
  field is gone. The storefront's locker picker is BoxNow's own iframe
  widget, which this API does not drive.
- **BoxNow is on only when its credentials and warehouse are set**: client
  id, secret, partner id and warehouse id
  (`shipping_boxnow.services.missing_credentials`, one rule for
  `is_configured` and the parcel creation). The warehouse goes out as
  `origin.locationId` and is never replaced by a stand-in; a store that
  left one empty has BoxNow hidden at checkout, and a parcel for an order
  placed before that raises `BoxNowConfigError` naming the field.
  `box_now_notify_phone` is optional (BoxNow's integration manual, §4.4
  "Request a delivery", asks for the sender's name only): it is sent as
  `origin.contactNumber` when set and omitted otherwise.
- Webhook handler: `BoxNowService.apply_webhook_event` — idempotent on
  `webhook_message_id`.
- The webhook view (`src/shipping_boxnow/views/webhook.py`) answers 400 (BoxNow
  does not retry) to any body it cannot read as a JSON object: bytes that are
  not UTF-8 (decoded strictly first, RFC 8259 §8.1 — `json.loads` on bytes
  would sniff UTF-16/32, while the signed-data extractor walks the raw bytes),
  invalid JSON, JSON nested past the decoder's stack, or valid JSON that is not
  an object (`[]`, `"x"`, `null`). Those used to raise inside the
  unauthenticated view and return 500, which BoxNow retries.
