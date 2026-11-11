# Order system: overview

Part of the order reference. [`docs/order/index.md`](index.md) lists every file
and says which file holds each § number used here.

## 1. Overview

Three orthogonal axes describe every order in the system:

| Axis | Values | Meaning |
|---|---|---|
| `Order.status` | `PENDING`, `PROCESSING`, `SHIPPED`, `DELIVERED`, `COMPLETED`, `CANCELED`, `RETURNED`, `REFUNDED` | The fulfilment state. |
| `Order.payment_status` | `PENDING`, `PROCESSING`, `COMPLETED`, `FAILED`, `REFUNDED`, `PARTIALLY_REFUNDED`, `CANCELED` | The financial state. **Decoupled from `status`** — important for COD where the parcel can be SHIPPED while payment is still PENDING. |
| `Order.pay_way.settlement` | `online`, `courier_cash`, `carrier_terminal`, `offline_transfer` | How the money changes hands. (continues under the table) |

**`Order.pay_way.settlement`.** Drives which create path runs (online → webhook
completes payment; the rest → collected later) AND which carriers may serve the
order — a carrier declares the settlements it can physically perform
(`supported_settlements`). The pay way's former `is_online_payment` /
`requires_confirmation` booleans are gone (`pay_way` 0034 took them out of the
model, 0035 dropped the columns). The order's `isOnlinePayment` is a different
field, computed from `settlement`.

Two order columns look like "the payment method" and are not
interchangeable:

- **`Order.pay_way_key`** — a snapshot of the `PayWayEnum` key the
  shopper chose, written by `Order.save()`. This is the customer-facing
  label; the storefront maps it through `payment_methods.*`. It is
  snapshotted because `pay_way` is `SET_NULL` and because renaming a
  key must not rewrite history.
- **`Order.payment_method`** — the GATEWAY that processed the charge
  (`stripe`, `viva_wallet`, `acs_cod`), written later by the
  payment-confirmation handlers and *matched on* by the Viva webhook
  and the ACS COD reconcile. Never render it to a customer.

Django-rendered surfaces (invoice PDF, merchant email, admin) use
`PayWay.display_name`, which resolves the key to a localised label. The
storefront gets the key instead and translates it itself: it owns the
payment-method labels, and a key stays stable across languages.

The admin order list's **Payment Method** column
(`OrderAdmin.payment_method_label`) is the one exception, because staff
want the PSP, not the pay-way name (Viva's seeded pay way is keyed
`CREDIT_CARD`): it shows `payment_provider_label(pay_way.provider_code)`
(`src/order/payment.py`, the provider class's `display_name`, never an
instance) and falls back to `get_pay_way_key_display()` for a method no
provider registers, such as cash on delivery. Both are in memory
(`pay_way` is `list_select_related`), so the column costs no query.

Each shipping provider (ACS, BoxNow, plus the built-in `flat_rate`
merchant delivery) is a `ShippingCarrierInterface` adapter
(`src/shipping/interfaces.py`) registered with `@register_provider` and
looked up via `get_provider(code)` by
`src/shipping/services.py::ShippingService`. The adapter pattern lets
new couriers plug in without touching the order code.
