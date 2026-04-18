"""Generate image assets used in the CV header.

* QR code PNG (from a target URL)
* Default "user avatar" placeholder PNG (simple silhouette)

Both are returned as ``data:image/png;base64,…`` URIs so they can be embedded
directly in the template and rendered by either WeasyPrint or xhtml2pdf
without worrying about external file paths.
"""

from __future__ import annotations

import base64
from io import BytesIO

import qrcode
import qrcode.constants
from PIL import Image, ImageDraw


def _png_data_uri(img: Image.Image) -> str:
    buf = BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def generate_qr_data_uri(url: str, size_px: int = 260) -> str:
    """Return a base64 PNG data URI for a QR encoding `url`.

    Uses medium error correction (~15% recoverable) — a good balance between
    size and resilience for printed CVs.
    """
    if not url or not url.strip():
        raise ValueError("QR URL is empty")
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    qr.add_data(url.strip())
    qr.make(fit=True)
    img = qr.make_image(fill_color="#0f172a", back_color="#ffffff").convert("RGB")
    # Resize so the PDF embedding is at a predictable pixel size (the template
    # uses width/height attributes to scale it).
    img = img.resize((size_px, size_px), Image.NEAREST)
    return _png_data_uri(img)


def photo_data_uri_from_file(path: str | "Path", size_px: int = 260) -> str:
    """Return a base64 PNG data URI from an image on disk, square-cropped
    and resized to ``size_px``. Used to embed a user's uploaded profile
    picture in the generated CV PDF.
    """
    from pathlib import Path as _Path
    img = Image.open(_Path(path))
    if img.mode != "RGB":
        img = img.convert("RGB")
    # Centre-crop to square so circular masks (CSS border-radius: 50%) and
    # square portrait cells both look right.
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    img = img.crop((left, top, left + side, top + side))
    img = img.resize((size_px, size_px), Image.LANCZOS)
    return _png_data_uri(img)


def generate_user_icon_data_uri(size_px: int = 260) -> str:
    """A simple neutral user-silhouette placeholder PNG."""
    img = Image.new("RGB", (size_px, size_px), "#ffffff")
    d = ImageDraw.Draw(img)

    outer_pad = 4
    # Outer ring / background
    d.ellipse(
        [(outer_pad, outer_pad), (size_px - outer_pad, size_px - outer_pad)],
        fill="#e2e8f0",
        outline="#94a3b8",
        width=3,
    )

    cx = size_px / 2
    # Head — a circle centred slightly above the middle.
    head_r = size_px * 0.18
    head_cy = size_px * 0.40
    d.ellipse(
        [(cx - head_r, head_cy - head_r), (cx + head_r, head_cy + head_r)],
        fill="#94a3b8",
    )
    # Shoulders — a pie-slice that looks like rounded shoulders.
    body_w = size_px * 0.35
    body_top = size_px * 0.62
    body_h = size_px * 0.40
    d.pieslice(
        [(cx - body_w, body_top), (cx + body_w, body_top + body_h * 2)],
        start=180,
        end=360,
        fill="#94a3b8",
    )
    return _png_data_uri(img)
