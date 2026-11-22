"""QR drawing for a pairing URL. The generator is vendored; there is no Node build."""

from __future__ import annotations

from foil_bridge.vendor.qrcodegen import QrCode


def svg(text: str) -> str:
    """One SVG document. Dark modules are the symbol; the page does not interpret it."""

    symbol = QrCode.encode_text(text, QrCode.Ecc.MEDIUM)
    size = symbol.get_size()
    border = 2
    span = size + border * 2
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {span} {span}" '
        'shape-rendering="crispEdges">',
        f'<rect width="{span}" height="{span}" fill="#f3ecdf"/>',
    ]
    for y in range(size):
        for x in range(size):
            if symbol.get_module(x, y):
                parts.append(
                    f'<rect x="{x + border}" y="{y + border}" width="1" height="1" fill="#241c14"/>'
                )
    parts.append("</svg>")
    return "".join(parts)
