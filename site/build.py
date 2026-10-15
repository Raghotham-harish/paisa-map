"""
build.py — render the business site (site/) to static HTML in business/.

    python3 site/build.py            # writes business/ (served at /business/)

Pages come from site/content.py (one dict per URL, a list of sections each);
prices are read from paisamap-etl/etl/_pricing.py so they can't drift from
billing. Output is plain HTML + CSS + WebP, one index.html per URL.
"""
import shutil
import sys
import time
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from PIL import Image

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
OUT = REPO / "business"
sys.path.insert(0, str(REPO / "paisamap-etl" / "etl"))
sys.path.insert(0, str(HERE))

import _pricing  # noqa: E402
import content  # noqa: E402

ORIGIN = "https://paisamaps.com"


def inr(paise):
    """Indian digit grouping: 1200000 paise -> ₹12,000."""
    n = str(paise // 100)
    if len(n) > 3:
        head, tail = n[:-3], n[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        n = ",".join(groups) + "," + tail
    return "₹" + n


def pricing_teaser():
    T = _pricing.TIERS
    lite = _pricing.SIGNAL_TIERS["lite"]
    return [
        {"tag": "Free", "name": "Free map", "amount": "₹0", "per": "",
         "blurb": "The map and three core metrics for every pincode.",
         "features": ["No sign-in needed", "Save locations with a free account", "Property rate (city-level estimate)"]},
        {"tag": "Signals", "name": lite["label"], "amount": inr(lite["price_paise"]), "per": "/ month",
         "blurb": f"{len(lite['extra_signals'])} more signals on the map.",
         "features": ["Banking, UPI, vehicles, tax", "Month to month", "Signals Pro " + inr(_pricing.SIGNAL_TIERS["pro"]["price_paise"]) + " for all"]},
        {"tag": "Dashboard", "name": T["starter"]["label"], "amount": inr(T["starter"]["price_paise"]), "per": "/ month",
         "blurb": "The workspace for one company.",
         "features": [f"{T['starter']['credits_per_month']:,} credits / month", f"{T['starter']['seats']} seats",
                      "Projects, compare, forecast, reports"]},
        {"tag": "Most teams", "name": T["growth"]["label"], "amount": inr(T["growth"]["price_paise"]), "per": "/ month",
         "highlight": True, "blurb": "More credits, more people, API access.",
         "features": [f"{T['growth']['credits_per_month']:,} credits / month", f"{T['growth']['seats']} seats",
                      "Higher API limits", "Free viewers"]},
    ]


def image_manifest(img_dir):
    """name -> {w, h (of the largest file), large, small (available widths)}.
    Files are <name>-<width>.webp; phone shots are narrower than 1600."""
    import re
    widths = {}
    for f in img_dir.glob("*.webp"):
        m = re.match(r"(.+)-(\d+)\.webp$", f.name)
        if m:
            widths.setdefault(m.group(1), []).append(int(m.group(2)))
    out = {}
    for name, ws in sorted(widths.items()):
        ws = sorted(set(ws))
        big = Image.open(img_dir / f"{name}-{ws[-1]}.webp")
        out[name] = {"w": big.width, "h": big.height, "large": ws[-1], "small": ws[0]}
    return out


def signal_groups():
    """content.SIGNAL_GROUPS with each signal's tier read from the code, and a
    check that every served signal is described (so a new one can't go missing)."""
    import _signals_data
    groups, seen = [], set()
    for g in content.SIGNAL_GROUPS:
        sigs = []
        for x in g["signals"]:
            tier = _signals_data.signal_tier_of(x["key"])
            sigs.append(dict(x, tier=tier))
            seen.add(x["key"])
        groups.append(dict(g, signals=sigs))
    missing = set(_signals_data.SIGNAL_COLUMNS) - seen
    if missing:
        raise SystemExit(f"signals served but not described on the site: {sorted(missing)}")
    return groups


def jsonld(page):
    blocks = [{
        "@context": "https://schema.org", "@type": "Organization", "name": "PaisaMaps",
        "url": ORIGIN + content.B, "logo": ORIGIN + "/assets/logo-box-v2.png",
    }]
    faqs = [s for s in page["sections"] if s["type"] == "faq"]
    if faqs:
        blocks.append({"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [
            {"@type": "Question", "name": q["q"], "acceptedAnswer": {"@type": "Answer", "text": q["a"]}}
            for s in faqs for q in s["items"]]})
    return blocks


def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "assets" / "img").mkdir(parents=True)
    for f in ("site.css", "site.js"):
        shutil.copy(HERE / "static" / f, OUT / "assets" / f)
    for f in (HERE / "static" / "img").glob("*.webp"):
        shutil.copy(f, OUT / "assets" / "img" / f.name)

    env = Environment(loader=FileSystemLoader(HERE / "templates"), autoescape=select_autoescape(["html"]),
                      trim_blocks=True, lstrip_blocks=True)
    site = {"origin": ORIGIN, "base": content.B, "nav": content.NAV, "footer": content.FOOTER,
            "year": date.today().year, "build": str(int(time.time())),
            "images": image_manifest(HERE / "static" / "img"),
            "signal_groups": signal_groups(), "ramps": content.RAMPS}
    tpl = env.get_template("page.html")

    urls = []
    for page in content.PAGES:
        for s in page["sections"]:
            if s["type"] == "pricing_teaser" and not s["plans"]:
                s["plans"] = pricing_teaser()
            for key in ("img", "left", "right", "shot"):  # top-level images; nested ones are checked by the template
                if s.get(key) and s[key] not in site["images"]:
                    raise SystemExit(f"{page['url']}: missing image {s[key]}")
        page = dict(page, jsonld=jsonld(page))
        html = tpl.render(site=site, page=page)
        rel = page["url"][len(content.B):]
        dest = OUT / rel / "index.html"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(html)
        urls.append(page["url"])
        print(f"  {page['url']:48} {len(html)//1024:>4} KB")

    sm = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    sm += [f"  <url><loc>{ORIGIN}{u}</loc><lastmod>{date.today().isoformat()}</lastmod></url>" for u in urls]
    sm.append("</urlset>")
    (OUT / "sitemap.xml").write_text("\n".join(sm) + "\n")
    print(f"built {len(urls)} page(s) → {OUT.relative_to(REPO)}/  ({sum(f.stat().st_size for f in OUT.rglob('*') if f.is_file())//1024} KB)")


if __name__ == "__main__":
    main()
