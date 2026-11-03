from __future__ import annotations

from datetime import date
from urllib.parse import urlparse

from flask import Blueprint, Response, current_app, request

bp = Blueprint("seo", __name__)


def site_base() -> str:
    configured = (current_app.config.get("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if configured:
        return configured
    return request.url_root.rstrip("/")


@bp.route("/robots.txt")
def robots():
    base = site_base()
    host = urlparse(base).netloc or request.host
    body = "\n".join(
        [
            "User-agent: *",
            "Allow: /",
            "Disallow: /admin",
            "Disallow: /cabinet",
            "Disallow: /lessons",
            "Disallow: /media",
            "Disallow: /logout",
            "",
            f"Host: {host}",
            f"Sitemap: {base}/sitemap.xml",
            "",
        ]
    )
    return Response(body, mimetype="text/plain; charset=utf-8")


@bp.route("/sitemap.xml")
def sitemap():
    base = site_base()
    today = date.today().isoformat()
    pages = (
        (f"{base}/", "weekly", "1.0"),
        (f"{base}/register", "monthly", "0.6"),
        (f"{base}/cookies", "yearly", "0.2"),
    )
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for loc, freq, priority in pages:
        lines.extend(
            [
                "  <url>",
                f"    <loc>{loc}</loc>",
                f"    <lastmod>{today}</lastmod>",
                f"    <changefreq>{freq}</changefreq>",
                f"    <priority>{priority}</priority>",
                "  </url>",
            ]
        )
    lines.append("</urlset>")
    return Response("\n".join(lines) + "\n", mimetype="application/xml; charset=utf-8")
