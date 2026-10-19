from __future__ import annotations


def qr_svg(url: str) -> str:
    """Inline SVG QR code for ``url``.

    Uses ``SvgPathImage`` so the QR is a single ``<path>`` element —
    WeasyPrint renders it as true vector geometry, no rasterisation
    and no PIL dependency on the render path. Returns an ``<svg>``
    string safe to drop into a template via ``|safe``.
    """
    import qrcode
    import qrcode.image.svg

    qr = qrcode.QRCode(
        image_factory=qrcode.image.svg.SvgPathImage,
        box_size=10,
        border=2,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image()
    return img.to_string(encoding="unicode")
