"""Promotion evaluation engine.

One engine serves every surface that needs to know "what discount does
this cart get": the cart serializer (preview), the pre-order
payment-intent amount, the order-create verification guard, and the
order-create application itself. All of them MUST agree to the cent —
``PaymentAmountMismatchError`` is the alarm that fires when they don't.

Value flow order (fixed): product markdown (already inside
``Product.final_price``) → promotions (this engine) → loyalty
redemption → gift-card payment. The engine never re-derives product
markdown; it discounts the already-marked-down cart line totals.

One module per seam, each importing only the ones below it:

- ``evaluation``: ``PromotionEngine``, the entry point (``evaluate``,
  ``evaluate_single_unit``, ``single_unit_discount``) and the
  classification every caller shares.
- ``teasers``: near-miss entries and the cart a teaser's advice builds.
- ``redemptions``: the redemption rows an order records.
- ``eligibility``: candidate collection and the per-promotion checks.
- ``benefits``: what a promotion is worth, and its free gift.
- ``stacking``: which promotions combine, and how each is allocated over
  the cart lines.
- ``bxgy``: the units a buy-X-get-Y discounts.
- ``scope``: the cart lines a promotion covers, categories expanded.
- ``results``: the value types an evaluation returns.
"""
