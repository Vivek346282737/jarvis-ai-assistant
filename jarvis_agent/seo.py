"""SEO/meta basics that do not need a model: a favicon (brand initial on the accent gradient, so it always matches the
theme with zero extra generation), robots.txt and a sitemap.xml built from the pages actually on disk. The guide also
tells the model to write Open Graph tags itself (those need real per-page copy, so they are not code-generated)."""
import os
import re

from . import util


def favicon_svg(name, accent, accent2):
    letter = (re.sub(r"[^A-Za-z0-9]", "", name or "")[:1] or "J").upper()
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
            f'<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{accent}"/><stop offset="1" stop-color="{accent2}"/></linearGradient></defs>'
            f'<rect width="64" height="64" rx="14" fill="url(#g)"/>'
            f'<text x="32" y="43" font-family="Arial,Helvetica,sans-serif" font-size="30" font-weight="700" fill="#fff" text-anchor="middle">{letter}</text></svg>')


def robots_txt(base_url=""):
    lines = ["User-agent: *", "Allow: /"]
    if base_url:
        lines.append(f"Sitemap: {base_url.rstrip('/')}/sitemap.xml")
    return "\n".join(lines) + "\n"


def sitemap_xml(pages, base_url):
    base = (base_url or "").rstrip("/")
    urls = "".join(f"<url><loc>{base}/{p if p != 'index.html' else ''}</loc></url>" for p in pages)
    return '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + urls + "</urlset>\n"


def install(root, name, accent, accent2, pages, base_url=""):
    """Writes favicon.svg, robots.txt, sitemap.xml at the project root. -> list of written relative paths."""
    files = {"favicon.svg": favicon_svg(name, accent, accent2), "robots.txt": robots_txt(base_url),
             "sitemap.xml": sitemap_xml(pages or ["index.html"], base_url)}
    out = []
    for fn, content in files.items():
        util.write_files(root, [{"path": fn, "content": content}])
        out.append(fn)
    return out


SEO_GUIDE = (
    'SEO: also add <link rel="icon" href="/favicon.svg" type="image/svg+xml"> in <head>, plus real Open Graph tags with content specific to THIS page - '
    '<meta property="og:title" content="..."><meta property="og:description" content="..."><meta property="og:type" content="website">'
    '<meta name="twitter:card" content="summary_large_image">. Use exactly one <h1> per page and a logical h2/h3 hierarchy - never skip levels or use headings for styling only.'
)
