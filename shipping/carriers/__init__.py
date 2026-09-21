"""Carrier adapters that ship with the platform itself.

Third-party integrations live in their own apps (``shipping_acs``,
``shipping_boxnow``) because they carry models, tasks and webhooks. The
adapters here have none of that — they are policy, not integration — and
they live inside ``shipping`` so that a deployment cannot end up without
them. ``flat_rate`` in particular is what makes a brand-new store able
to take an order at all.
"""
