"""Every page except the home page. Same section vocabulary as content.HOME.
Copy rules (docs/BUSINESS_SITE_IA.md): real numbers only, scores are modelled
estimates, no invented customers, no claims of real-time or footfall data."""
import _pricing as P
from content import B, SOURCES

CONTACT = {"label": "Talk to us", "url": B + "contact/"}
MAP = {"label": "Open the free map", "url": "/"}
PRICING = {"label": "See pricing", "url": B + "pricing/"}


def inr(paise):
    n = str(int(paise) // 100)
    if len(n) > 3:
        head, tail, groups = n[:-3], n[-3:], []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        n = ",".join(groups) + "," + tail
    return "₹" + n


def crumbs(*pairs):
    return [{"label": "Business", "url": B}] + [{"label": l, "url": u} for l, u in pairs]


def cta(title="See your own market on the map", lede="Tell us the cities and the decision you're making. "
        "We'll walk through it with you on the real data in about 20 minutes."):
    return {"type": "cta", "title": title, "lede": lede, "ctas": [CONTACT, MAP]}


USP_TILES = [
    {"cls": "green wide", "num": "Insight", "title": "See it",
     "text": "Purchasing power, income, spend and 15 signals for every pincode, on a map you can read in seconds."},
    {"cls": "amber", "num": "Compare", "title": "Weigh it",
     "text": "Put up to five sites side by side: scores, income, spend and how they rank."},
    {"cls": "magenta", "num": "Decide", "title": "Commit to it",
     "text": "Turn a budget into ranked sites and a revenue range, then send a report to whoever signs off."},
    {"cls": "wide", "icon": "⇅", "title": "Grounded in your own stores",
     "text": "Upload your store list and see which signals your best stores share. New sites are scored against that."},
    {"cls": "", "icon": "✓", "title": "Labelled honestly",
     "text": "Modelled numbers say so. Property rate is marked as a city-level estimate."},
    {"cls": "", "icon": "⟳", "title": "Checked every week",
     "text": "The model is refitted weekly and must pass fixed sanity checks before the map changes."},
]

# ─────────────────────────────── Product ───────────────────────────────

PRODUCT = {
    "url": B + "product/", "og_image": "ws-compare",
    "title": "PaisaMaps platform: map, signals, workspace and forecast",
    "description": "A tour of PaisaMaps: a live purchasing-power map, a palette of 15 signals, a team workspace "
                   "for comparing sites, an expansion forecast, reports and an API.",
    "sections": [
        {"type": "hero", "crumbs": crumbs(), "crumb_here": "Product", "kicker": "The platform",
         "title": 'From <span class="accent">insight</span> to a decision, in one place.',
         "lede": "Read a city's spending power on the map, compare the sites you're considering, and turn a budget "
                 "into a ranked plan your team can sign off.",
         "ctas": [CONTACT, MAP], "shot": "ws-compare", "shot_alt": "Comparing four Bengaluru sites side by side",
         "chips": [{"pos": "br", "big": "4 sites", "text": "compared on opportunity, income and spend"}]},
        {"type": "bento", "kicker": "Why teams use it", "title": "Insight, comparison, decision", "tiles": USP_TILES},
        {"type": "splits", "kicker": "What's inside", "title": "Six tools that work together", "rows": [
            {"img": "mum-choropleth-ppi", "kicker": "Live map", "title": "Every pincode's spending power",
             "text": "Search a city or pincode and read it six ways: choropleth, heatmap, bubbles, clusters, bivariate or top-N.",
             "ctas": [{"label": "The map", "url": B + "product/map/"}]},
            {"img": "map-catalog", "kicker": "Signal palette", "title": "18 signals, each traced to a source",
             "text": "Banking, UPI, tax, vehicles, night-lights and more. Switch any of them on the map.",
             "ctas": [{"label": "Signals & data", "url": B + "product/signals/"}]},
            {"img": "ws-map", "kicker": "Map workspace", "title": "Click a pincode, get a verdict",
             "text": "Location intelligence for the pincode you pick: opportunity score, how it compares with the district and state, and the signals behind it.",
             "ctas": [{"label": "The workspace", "url": B + "product/dashboard/"}]},
            {"img": "ws-compare", "kicker": "Compare", "title": "Shortlists you can defend",
             "text": "Line up the candidates and see which leads on opportunity, income and spend, and by how much."},
            {"img": "ws-forecast", "kicker": "Forecast", "title": "A budget becomes a plan",
             "text": "Enter what you can invest and get ranked sites, reachable revenue and payback, with every assumption shown.",
             "ctas": [{"label": "The forecast", "url": B + "product/forecast/"}]},
            {"img": "ws-customer-data", "kicker": "Your data", "title": "Your stores, scored",
             "text": "Upload a CSV or Excel file of your stores with revenue. Each one is scored against its area, and the forecast learns from them.",
             "ctas": [{"label": "Exports & API", "url": B + "product/api/"}]},
        ]},
        {"type": "signals", "kicker": "The signal palette", "title": "What the map is made of",
         "lede": "Three core metrics anyone can see, plus fifteen signals on paid plans. Each colour bar is the ramp that signal uses on the map.",
         "ctas": [{"label": "How we capture signals", "url": B + "product/signals/"}]},
        cta(),
    ],
}

MAP_PAGE = {
    "url": B + "product/map/", "og_image": "blr-bubble-ppi",
    "title": "Live purchasing-power map of India by pincode | PaisaMaps",
    "description": "Search any Indian city or pincode and see purchasing power, income and spend as a choropleth, "
                   "heatmap, bubbles, clusters, bivariate or top-N view. Free, no sign-in.",
    "sections": [
        {"type": "hero", "crumbs": crumbs(("Product", B + "product/")), "crumb_here": "Live map", "kicker": "Live map",
         "title": 'Read a whole city\'s <span class="accent">spending power</span> at a glance.',
         "lede": "15,331 pincodes, coloured by purchasing power, household income or spend. Free to use, no sign-in.",
         "ctas": [MAP, CONTACT], "meta": ["Six map views", "18 signals", "Works on your phone"],
         "shots": [
             {"img": "blr-bubble-ppi", "title": "Bubbles", "caption": "Bengaluru, size shows the value"},
             {"img": "mum-choropleth-ppi", "title": "Choropleth", "caption": "Mumbai, coloured by purchasing power"},
             {"img": "hyd-heatmap-ppi", "title": "Heatmap", "caption": "Hyderabad, purchasing power"},
             {"img": "blr-bivariate", "title": "Bivariate", "caption": "Bengaluru, two signals at once"},
         ]},
        {"type": "carousel", "kicker": "Six views", "title": "Pick the view that fits the question",
         "lede": "The same data, drawn differently. Swipe or use the arrows.",
         "shots": [
             {"img": "pune-choropleth-ppi", "title": "Choropleth", "caption": "real PIN boundaries, Pune"},
             {"img": "blr-heatmap-income", "title": "Heatmap", "caption": "where income concentrates, Bengaluru"},
             {"img": "mum-cluster-ppi", "title": "Clusters", "caption": "grouped at low zoom, Mumbai"},
             {"img": "blr-bivariate", "title": "Bivariate", "caption": "fill and border carry two signals"},
             {"img": "del-choropleth-spend", "title": "Spend", "caption": "household spend, Delhi NCR"},
             {"img": "full-mum-app", "title": "Ranked list", "caption": "localities ranked beside the map"},
         ]},
        {"type": "compare", "kicker": "Switch the metric", "title": "Income and purchasing power tell different stories",
         "lede": "Drag across Mumbai. Purchasing power blends income and spend, ranked against the region.",
         "bullets": ["One click between PPI, income and spend", "Colours are ranked within the region",
                     "Click a pincode for its numbers"],
         "left": "mum-choropleth-income", "left_label": "Income", "right": "mum-choropleth-ppi", "right_label": "Purchasing power"},
        {"type": "splits", "rows": [
            {"img": "map-explorer", "kicker": "Signal explorer", "title": "Search and switch signals",
             "text": "Find a signal by name or category and paint the map with it, in the colour ramp of its group."},
            {"img": "mobile-blr-app", "phone": True, "kicker": "On your phone", "title": "Take it to the site visit",
             "text": "The map works on a phone, so you can check an area while you're standing in it."},
        ]},
        {"type": "ramps", "kicker": "Colour that means something", "title": "Each metric has its own palette",
         "lede": "Two-hue scales so the map re-reads instantly when you switch, and the legend always matches the map."},
        {"type": "faq", "title": "About the map", "items": [
            {"q": "Is the map really free?", "a": "Yes. Anyone can open it, search and read purchasing power, income and spend without signing in."},
            {"q": "Why are some areas circles instead of shapes?", "a": "We draw India Post's own PIN boundaries. Where a pincode has no published boundary, it keeps a circle rather than borrowing a neighbour's shape."},
            {"q": "What does 100 mean?", "a": "Purchasing power is indexed so the regional average is 100. 130 means about 30% above the region; 70 means about 30% below."},
        ]},
        cta(),
    ],
}

DASHBOARD = {
    "url": B + "product/dashboard/", "og_image": "ws-dashboard",
    "title": "The PaisaMaps workspace: projects, compare, store data and reports",
    "description": "A shared workspace for expansion decisions: projects, location intelligence, site comparison, "
                   "saved-location pipeline, your own store data and PDF reports.",
    "sections": [
        {"type": "hero", "crumbs": crumbs(("Product", B + "product/")), "crumb_here": "Workspace", "kicker": "The workspace",
         "title": 'Where your team <span class="accent">decides</span>.',
         "lede": "Projects, a map workspace, comparison, your own store data and reports, shared by everyone on the account.",
         "ctas": [CONTACT, PRICING], "meta": ["7-day trial, no card", "Free viewers", "Encrypted store data"],
         "shots": [
             {"img": "ws-dashboard", "title": "Dashboard", "caption": "pick up where you left off"},
             {"img": "ws-map", "title": "Map workspace", "caption": "location intelligence on click"},
             {"img": "ws-compare", "title": "Compare", "caption": "four sites side by side"},
         ]},
        {"type": "bento", "kicker": "Built for decisions", "title": "Insight, comparison, decision", "tiles": USP_TILES},
        {"type": "splits", "kicker": "Post-login", "title": "What you can do once you're in", "rows": [
            {"img": "ws-map", "kicker": "Insight", "title": "Location intelligence on every click",
             "text": "Click any pincode in the map workspace: opportunity score, how it compares with its district, state and neighbours, estimated income and spend, and the signals behind it.",
             "bullets": ["Colour the map by your project's suitability", "Distance rings around a site", "Your own stores drawn on the map"],
             "chips": [{"pos": "tl", "big": "100 / 100", "text": "opportunity for Ulsoor, Bengaluru (demo)"}]},
            {"img": "ws-compare", "kicker": "Comparison", "title": "Compare up to five sites",
             "text": "Pick candidates on the map and compare them on opportunity, economics, income and spend, with a one-line verdict on which leads and why.",
             "bullets": ["Ranked cards with suitability and risk", "Bars for each metric", "Save the shortlist to the project in one click"]},
            {"img": "ws-locations", "kicker": "Pipeline", "title": "Every candidate, with a status",
             "text": "Saved locations move through shortlist, reviewing, approved and rejected, with notes your team can see."},
            {"img": "ws-customer-data", "kicker": "Your data", "title": "Your stores, scored against their areas",
             "text": "Upload a CSV or Excel file with store, pincode and revenue. Each store gets a score, and the forecast calibrates to how your stores actually perform."},
            {"img": "ws-wizard", "kicker": "Set-up", "title": "A project in four steps",
             "text": "Company and industry, data, signals and market, then investment and outcome. It saves as you go."},
            {"img": "ws-project", "kicker": "Project", "title": "One place per expansion plan",
             "text": "Saved locations with their scores, the latest forecast, reports and who's working on it."},
        ]},
        {"type": "steps", "kicker": "Teams", "title": "Built for a team, priced for one",
         "steps": [
             {"title": "Seats across the account", "text": "One seat per person, however many of your companies they work in."},
             {"title": "Free viewers", "text": "Leaders and partners can see everything without using a seat or spending credits."},
             {"title": "Budgets and allowances", "text": "Cap what each company or person can spend from the shared credits."},
             {"title": "Reports to share", "text": "Generate a PDF for the board, or a read-only link."},
         ]},
        cta("See the workspace with your own cities"),
    ],
}

SIGNALS_PAGE = {
    "url": B + "product/signals/", "og_image": "map-catalog",
    "title": "Signals & data: what PaisaMaps measures and where it comes from",
    "description": "The PaisaMaps signal palette: purchasing power, income, spend and 15 signals across banking, "
                   "tax, infrastructure, vehicles and property, each with its source and geographic level.",
    "sections": [
        {"type": "hero", "crumbs": crumbs(("Product", B + "product/")), "crumb_here": "Signals & data", "kicker": "Signal capturing",
         "title": 'A palette of <span class="accent">18 signals</span>, each one traceable.',
         "lede": "We collect public data on banking, tax, vehicles, satellite night-lights, digital payments and more, "
                 "join it to each pincode, and combine it into purchasing power, income and spend.",
         "ctas": [CONTACT, MAP], "shot": "map-catalog", "shot_alt": "The signals catalog with coverage for each signal",
         "chips": [{"pos": "br", "big": "15,331", "text": "pincodes with the core metrics"}]},
        {"type": "signals", "kicker": "The palette", "title": "Every signal, its source and its level",
         "lede": "The level tells you how fine-grained a signal is. Pincode-level signals vary street to street; state-level ones set the wider context.",
         "note": "Signals measured at district or state level are the same for every pincode inside that area. The purchasing-power model "
                 "combines them with pincode-level ones; it doesn't pretend they are pincode measurements."},
        {"type": "steps", "kicker": "How a score is built", "title": "From public data to a pincode score",
         "steps": [
             {"title": "Collect", "text": "Pull each source in its published form, keep its licence terms, and record when it was fetched."},
             {"title": "Join", "text": "Attach each value to pincodes using India Post's PIN boundaries and directory."},
             {"title": "Model", "text": "Combine the signals into purchasing power, then estimate income and spend, calibrated to the national household survey."},
             {"title": "Check", "text": "A weekly refit must pass fixed sanity gates (for example, known affluent areas must outrank their neighbours) before anything changes."},
         ]},
        {"type": "marquee", "kicker": "Sources", "title": "Where the signals come from", "items": SOURCES,
         "note": "We don't sell OpenStreetMap-derived data, and we don't claim real-time, transaction-level or footfall data."},
        {"type": "ramps", "kicker": "On the map", "title": "Colour ramps for the core metrics",
         "lede": "Every signal group also has its own ramp, shown on the cards above."},
        {"type": "faq", "title": "About the data", "items": [
            {"q": "Are these real incomes?", "a": "No. They are modelled estimates built from public data. They are good for comparing areas, not for judging a single household."},
            {"q": "How often does it change?", "a": "The model is refitted weekly. Each source updates on its own schedule; some publish yearly."},
            {"q": "Can I add my own data?", "a": "Yes. Upload your store list with revenue in the workspace. It stays in your account, encrypted, and is used to score your stores and calibrate your forecast."},
            {"q": "Which signals come with which plan?", "a": "The three core metrics and the property-rate estimate are free. Signals Lite adds ten signals for ₹200 a month, and Signals Pro or any dashboard plan includes all of them."},
        ]},
        cta(),
    ],
}

FORECAST = {
    "url": B + "product/forecast/", "og_image": "ws-forecast",
    "title": "Expansion forecast: from a budget to ranked sites | PaisaMaps",
    "description": "Enter an investment budget and get ranked sites, reachable revenue, payback and a budget split, "
                   "with every assumption shown and calibrated to your own stores.",
    "sections": [
        {"type": "hero", "crumbs": crumbs(("Product", B + "product/")), "crumb_here": "Forecast", "kicker": "Expansion forecast",
         "title": 'Turn a <span class="accent">budget</span> into a ranked plan.',
         "lede": "Tell us what you can invest. The forecast picks sites, estimates reachable revenue and payback, and shows every assumption.",
         "ctas": [CONTACT, PRICING], "shot": "ws-forecast", "shot_alt": "The forecast page with revenue curve and key numbers"},
        {"type": "splits", "rows": [
            {"img": "ws-forecast", "kicker": "The curve", "title": "See where returns flatten",
             "text": "Investment against reachable revenue, with the point where each extra store adds less.",
             "bullets": ["Added revenue per month", "Stores the budget funds", "Blended payback"]},
            {"img": "ws-forecast-lower", "kicker": "The sites", "title": "Why each site made the list",
             "text": "Top sites compared on operating factors (supply chain, distribution, transport, mobility, ease of business) and a recommended budget split.",
             "bullets": ["Radar of each site's profile", "Factor table with gaps to the national median", "Budget split by lever"]},
        ]},
        {"type": "steps", "kicker": "How it works", "title": "Four steps, all visible",
         "steps": [
             {"title": "Budget", "text": "Your investment and how long you'll give each store."},
             {"title": "Reach", "text": "Households and spend in each candidate's catchment, from the signals."},
             {"title": "Capture", "text": "A share of that spend a new store can win, from benchmarks or your own stores."},
             {"title": "Revenue range", "text": "Ranked sites with revenue and payback, labelled as estimates."},
         ]},
        {"type": "html", "html": '<p class="note">Until you upload at least three stores with revenue, the forecast uses benchmark capture rates and says so on screen. Once your stores are in, it calibrates to how they actually perform.</p>'},
        cta("Run the forecast on your next city"),
    ],
}

API = {
    "url": B + "product/api/", "og_image": "map-catalog",
    "title": "Data API & exports: pincode purchasing power for your systems | PaisaMaps",
    "description": "Export purchasing power, income, spend and signals by pincode as CSV, Excel or JSON, or pull them "
                   "with an API key. Higher limits on Growth and above.",
    "sections": [
        {"type": "hero", "crumbs": crumbs(("Product", B + "product/")), "crumb_here": "Data API & exports", "kicker": "Data API & exports",
         "title": 'The same data, <span class="accent">in your systems</span>.',
         "lede": "Download pincode-level data as CSV, Excel or JSON, or call the export API with a key. "
                 "You only ever get the signals your plan includes.",
         "ctas": [CONTACT, PRICING], "shot": "map-catalog", "shot_alt": "The signals catalog"},
        {"type": "html", "html": '''<div class="split" style="padding:0">
<div><p class="kicker">One request</p><h2>Export by city, radius or all of India</h2>
<p class="lede">Pick the signals, the area and the format. API keys belong to your company, so admins can see and revoke every key.</p>
<ul class="ticks"><li>CSV, Excel or JSON</li><li>Choose the columns you need</li><li>Keys per company, revocable any time</li><li>Rate limits rise with your plan</li></ul></div>
<pre style="margin:0;overflow-x:auto;background:var(--dark);color:var(--on-dark);border-radius:var(--radius-lg);padding:20px;font:13px/1.6 ui-monospace,Menlo,monospace"><code>curl -H "X-API-Key: $PAISAMAPS_KEY" \\
  "https://paisamaps.com/api/export?format=csv&amp;dataset=ppi\\
&amp;scope=view&amp;lat=12.97&amp;lng=77.59&amp;radius_km=10\\
&amp;columns=bank_branches_per_lakh,upi_txn_value_per_capita"

pincode,name,ppi_ml,est_monthly_income_hh,…
560038,Indiranagar,…</code></pre></div>'''},
        {"type": "table", "kicker": "Access by plan", "title": "What each plan can pull",
         "head": ["", "Free", "Signals Lite / Pro", "Starter", "Growth", "Scale and up"],
         "rows": [["Core metrics", "✓", "✓", "✓", "✓", "✓"],
                  ["Signals", "—", "10 / all", "All", "All", "All"],
                  ["Exports from the map", "✓", "✓", "✓", "✓", "✓"],
                  ["API key", "Standard limit", "Standard limit", "Standard limit", "Higher limit", "Full access"]],
         "footnote": "Limits are per company, so extra keys don't add throughput. Talk to us if you need bulk feeds."},
        cta("Need a data feed? Tell us the format"),
    ],
}

# ─────────────────────────────── Solutions ───────────────────────────────

INDUSTRIES = [
    ("retail-fnb", "Retail & F&B", "Where to open the next store", "hero-blr-choropleth-ppi"),
    ("real-estate-hospitality", "Real estate & hotels", "Demand around a project or property", "mum-choropleth-income"),
    ("lending-fintech", "Lenders, NBFC & fintech", "Area-level affordability and market size", "del-choropleth-spend"),
    ("any-business", "Any business", "Know where your customers can afford you", "pune-choropleth-ppi"),
    ("agencies-consultants", "Agencies & consultants", "One account, every client", "mum-cluster-ppi"),
]
USE_CASES = [
    ("site-selection", "Site selection", "Rank candidate pincodes for a new location", "ws-compare"),
    ("market-sizing", "Market sizing", "Households × spend for a city, region or radius", "blr-heatmap-income"),
    ("catchment-analysis", "Catchment analysis", "Who lives around a site and what they spend", "ws-map"),
    ("credit-affordability", "Credit & affordability", "Area affordability as a lending input", "del-choropleth-spend"),
    ("territory-planning", "Territory planning", "Balance sales and delivery territories", "mum-cluster-ppi"),
    ("portfolio-benchmarking", "Portfolio benchmarking", "Score the stores and branches you already have", "ws-customer-data"),
]


def industry_cards():
    return [{"url": B + f"solutions/{s}/", "title": n, "text": t, "img": i, "tag": "Industry"} for s, n, t, i in INDUSTRIES]


def usecase_cards(only=None):
    return [{"url": B + f"use-cases/{s}/", "title": n, "text": t, "img": i, "tag": "Use case"}
            for s, n, t, i in USE_CASES if not only or s in only]


SOLUTIONS_HUB = {
    "url": B + "solutions/", "og_image": "hero-blr-choropleth-ppi",
    "title": "Solutions by industry and use case | PaisaMaps",
    "description": "How retail and F&B chains, real estate and hotel groups, lenders and fintechs, agencies and any "
                   "business use pincode purchasing power to decide where.",
    "sections": [
        {"type": "hero", "crumbs": crumbs(), "crumb_here": "Solutions", "kicker": "Solutions",
         "title": 'Two ways in: <span class="accent">who you are</span>, or what you need to decide.',
         "lede": "Start with your industry, or jump straight to the job: choosing a site, sizing a market, planning territories.",
         "ctas": [CONTACT, MAP], "shot": "ws-map", "shot_alt": "Map workspace with location intelligence"},
        {"type": "cards", "kicker": "By industry", "title": "Built around five kinds of buyer", "cards": industry_cards(), "carousel": True},
        {"type": "cards", "kicker": "By use case", "title": "Six decisions it helps with", "cards": usecase_cards()},
        cta(),
    ],
}


def industry_page(slug, name, kicker, title, lede, hero_img, questions, views, groups, cases, faqs, meta):
    return {
        "url": B + f"solutions/{slug}/", "og_image": hero_img,
        "title": f"{name}: {kicker} | PaisaMaps",
        "description": lede,
        "sections": [
            {"type": "hero", "crumbs": crumbs(("Solutions", B + "solutions/")), "crumb_here": name, "kicker": f"For {name.lower()}",
             "title": title, "lede": lede, "ctas": [CONTACT, MAP], "meta": meta, "shots": views[:4]},
            {"type": "splits", "kicker": "Your questions", "title": "What you ask, and where the answer is", "rows": questions},
            {"type": "carousel", "kicker": "Views you'll use", "title": "The screens behind the answers", "shots": views},
            {"type": "signals", "kicker": "Signals that matter here", "title": "The part of the palette you'll lean on",
             "groups": groups, "lede": "Every plan sees the core metrics; these are the signal groups most useful for your decisions."},
            {"type": "cards", "kicker": "Use cases", "title": "Jobs it does for you", "cards": usecase_cards(cases)},
            {"type": "faq", "title": "Questions we hear", "items": faqs},
            cta(),
        ],
    }


Q = lambda img, k, t, text, bullets=None: {"img": img, "kicker": k, "title": t, "text": text, "bullets": bullets or []}
V = lambda img, t, c: {"img": img, "title": t, "caption": c}

RETAIL = industry_page(
    "retail-fnb", "Retail & F&B", "where to open the next store",
    'Open where people <span class="accent">can spend</span>, not just where there\'s space.',
    "Retail, café, QSR and pharmacy chains use PaisaMaps to rank candidate pincodes, compare shortlisted sites, "
    "and turn an expansion budget into a plan their own store data backs up.",
    "hero-blr-choropleth-ppi",
    [Q("hero-blr-choropleth-ppi", "Where to look", "Which neighbourhoods can support our ticket size?",
       "Colour the city by purchasing power, income or spend and see the ranked list beside the map.",
       ["Purchasing power, income and spend per pincode", "Ranked localities for the area you're viewing"]),
     Q("ws-compare", "Which site", "Which of these five sites is strongest?",
       "Compare candidates on opportunity, income and spend, with a verdict on which leads and why.",
       ["Suitability and risk for each site", "Save the shortlist to your project"]),
     Q("ws-customer-data", "What works for us", "What do our best stores have in common?",
       "Upload your store list with revenue. See each store's score and which signals your strongest stores share."),
     Q("ws-forecast", "How much", "How many stores can this budget fund, and what will they earn?",
       "The forecast turns your budget into ranked sites, reachable revenue and payback.")],
    [V("hero-blr-choropleth-ppi", "Ranked localities", "Bengaluru, purchasing power"),
     V("ws-map", "Location intelligence", "verdict on click"), V("ws-compare", "Compare", "four sites side by side"),
     V("ws-forecast-lower", "Forecast", "why each site made the list"), V("blr-heatmap-income", "Heatmap", "income density")],
    ["Purchasing power", "Banking & UPI", "Vehicles"],
    ["site-selection", "catchment-analysis", "portfolio-benchmarking", "market-sizing"],
    [{"q": "Can it tell us footfall?", "a": "No. We don't have footfall or transaction data. We tell you what households around a site can spend, which complements the footfall counts you already take."},
     {"q": "We already have a GIS team. Why add this?", "a": "Because building and maintaining pincode-level purchasing power from public sources is the slow part. Use our exports and API in your own tools."},
     {"q": "How do we start?", "a": "Open the free map for your cities, then start the 7-day trial (no card) to compare sites and run the forecast."}],
    ["Café & QSR", "Apparel & lifestyle", "Pharmacy & grocery"])

REAL_ESTATE = industry_page(
    "real-estate-hospitality", "Real estate & hotels", "catchment demand for projects and properties",
    'Prove the <span class="accent">demand</span> around a project before you build it.',
    "Developers, leasing teams, mall operators and hotel chains read the spending power of the pincodes around a "
    "project, compare candidate plots and share the evidence with investors and partners.",
    "mum-choropleth-income",
    [Q("mum-choropleth-income", "Demand", "Can the catchment afford what we're building?",
       "See estimated household income and spend for every pincode around a site, against the rest of the city."),
     Q("ws-map", "Catchment", "What's within 3 km of this plot?",
       "Draw a distance ring around a site and read the opportunity and signals of what falls inside."),
     Q("ws-compare", "Plots", "Which of our candidate plots has the strongest catchment?",
       "Compare plots side by side on purchasing power, income and spend."),
     Q("ws-project", "Evidence", "How do we show investors the case?",
       "Keep plots, notes and reports in one project and share a PDF or read-only link.")],
    [V("mum-choropleth-income", "Income", "Mumbai"), V("ws-map", "Catchment", "distance rings"),
     V("blr-bivariate", "Bivariate", "income and spend together"), V("ws-compare", "Compare", "plot shortlist")],
    ["Purchasing power", "Property", "Infrastructure", "Vehicles"],
    ["catchment-analysis", "site-selection", "market-sizing"],
    [{"q": "Is the property rate based on transactions?", "a": "No. It's a city-level estimate, labelled as such, and free on every plan. Use it as context, not as a valuation."},
     {"q": "Does it work for hotels?", "a": "Yes, for reading the spending power of the surrounding area. It doesn't include tourism or occupancy data."},
     {"q": "Can we share it with partners?", "a": "Yes. Generate a PDF report or a read-only project link, or add partners as free viewers."}],
    ["Residential & commercial", "Malls & leasing", "Hotel chains"])

LENDING = industry_page(
    "lending-fintech", "Lenders, NBFC & fintech", "area-level affordability and market size",
    'Add <span class="accent">area affordability</span> to how you source and size.',
    "Banks, NBFCs, MFIs and fintechs use pincode-level income, spend and banking depth to prioritise sourcing, "
    "size new products by region and add an area signal alongside their own bureau data.",
    "del-choropleth-spend",
    [Q("del-choropleth-spend", "Where to source", "Which pincodes can afford this product?",
       "Rank pincodes by estimated income and spend, and see how banked they already are."),
     Q("map-catalog", "Which signals", "What feeds our model?",
       "Banking branches, deposits, UPI value, tax filers and vehicle ownership, each with its source and level."),
     Q("map-explorer", "Explore", "Where is banking thin but spending strong?",
       "Switch between purchasing power and branch density on the map and compare the two pictures."),
     Q("ws-customer-data", "Your book", "How does our existing book map onto areas?",
       "Upload branches or customer pincodes and see each one against its area's signals.")],
    [V("del-choropleth-spend", "Spend", "Delhi NCR"), V("map-catalog", "Catalog", "signals with coverage"),
     V("map-explorer", "Explorer", "switch signals"), V("blr-heatmap-income", "Income", "Bengaluru heatmap")],
    ["Purchasing power", "Banking & UPI", "Tax & economy"],
    ["credit-affordability", "market-sizing", "territory-planning"],
    [{"q": "Can this be used for individual credit decisions?", "a": "It's an area-level signal: the same for everyone in a pincode. Use it alongside your own customer-level data and your model governance, not instead of them."},
     {"q": "How do we get it into our models?", "a": "Export by pincode as CSV, Excel or JSON, or pull it with an API key. Growth and above get higher API limits."},
     {"q": "Where do the banking signals come from?", "a": "RBI branch and deposit statistics, and PhonePe Pulse for UPI value. The signals page lists each source and level."}],
    ["Banks & NBFCs", "MFIs", "Fintech & BNPL"])

ANY = industry_page(
    "any-business", "Any business", "know where your customers can afford you",
    'Find the customers who <span class="accent">can afford you</span>.',
    "D2C brands, franchises, clinics, schools, service businesses and SMEs use the same map to pick cities, plan "
    "delivery zones and size a new market, without a GIS team.",
    "pune-choropleth-ppi",
    [Q("pune-choropleth-ppi", "Which city", "Which city or neighbourhood should we try next?",
       "Compare cities and neighbourhoods on purchasing power in a few clicks, free."),
     Q("blr-heatmap-income", "Where to deliver", "Where should delivery or service coverage go first?",
       "See where income concentrates and draw your zones around it."),
     Q("ws-locations", "Shortlist", "How do we keep track of options?",
       "Save locations, tag them as shortlist, reviewing, approved or rejected, and add notes."),
     Q("ws-forecast", "Budget", "What can our budget support?",
       "Run the forecast with your investment and see a ranked plan.")],
    [V("pune-choropleth-ppi", "Pune", "purchasing power"), V("blr-heatmap-income", "Heatmap", "income"),
     V("mobile-blr-app", "On your phone", "Bengaluru"), V("ws-locations", "Saved locations", "your shortlist")],
    ["Purchasing power", "Banking & UPI", "Vehicles"],
    ["market-sizing", "site-selection", "territory-planning"],
    [{"q": "We're small. Is this for us?", "a": "Yes. The map is free, a signal plan is ₹200 a month, and the dashboard plans start with a 7-day trial and no card."},
     {"q": "Do we need data skills?", "a": "No. If you can use a map, you can use PaisaMaps. Exports are there when you want them."},
     {"q": "Can we cancel any time?", "a": "Yes. Monthly plans run month to month."}],
    ["D2C & franchises", "Clinics & schools", "Services & SMEs"])

AGENCIES = industry_page(
    "agencies-consultants", "Agencies & consultants", "one account for every client",
    'One account, <span class="accent">every client</span>, clean billing.',
    "Business-strategy firms, consultancies, research and media agencies run each client as its own company, "
    "paid from one wallet, with budgets per client and usage reported client by client.",
    "mum-cluster-ppi",
    [Q("ws-dashboard", "Clients", "How do we keep clients separate?",
       "Each client is its own company with its own projects and data. Switch between them from the header."),
     Q("ws-compare", "Deliverables", "What do we hand over?",
       "Comparisons, PDF reports, read-only links and exports your client can open without a login."),
     Q("ws-customer-data", "Client data", "Can we use a client's store data?",
       "Upload it into that client's company only. It's encrypted and never mixed with another client's."),
     Q("ws-project", "Budgets", "How do we control spend per client?",
       "Set a credit budget per client company, and allowances per person.")],
    [V("mum-cluster-ppi", "Clusters", "Mumbai"), V("ws-compare", "Compare", "client shortlist"),
     V("ws-project", "Project", "per client"), V("ws-forecast-lower", "Forecast", "factor profile")],
    ["Purchasing power", "Banking & UPI", "Tax & economy", "Infrastructure"],
    ["market-sizing", "site-selection", "portfolio-benchmarking"],
    [{"q": "Can one wallet pay for several clients?", "a": "Yes. Link client companies to your account; credits are drawn from your wallet and usage is reported per client."},
     {"q": "Do our clients need seats?", "a": "Seats are counted across your whole account. Client stakeholders can join as free viewers."},
     {"q": "Can we white-label?", "a": "Not today. Talk to us about bulk data feeds for your own tools."}],
    ["Strategy & consulting", "Research agencies", "Media & marketing"])

# ─────────────────────────────── Use cases ───────────────────────────────


def usecase_page(slug, name, title, lede, hero_img, steps, rows, industries, groups):
    return {
        "url": B + f"use-cases/{slug}/", "og_image": hero_img,
        "title": f"{name} with pincode purchasing power | PaisaMaps",
        "description": lede,
        "sections": [
            {"type": "hero", "crumbs": crumbs(("Solutions", B + "solutions/")), "crumb_here": name, "kicker": f"Use case · {name}",
             "title": title, "lede": lede, "ctas": [CONTACT, MAP], "shot": hero_img, "shot_alt": name},
            {"type": "steps", "kicker": "How it's done", "title": f"{name} in four steps", "steps": steps},
            {"type": "splits", "rows": rows},
            {"type": "signals", "kicker": "Signals used", "title": "What feeds this", "groups": groups},
            {"type": "cards", "kicker": "Who does this", "title": "Industries that use it",
             "cards": [c for c in industry_cards() if c["url"].rstrip("/").split("/")[-1] in industries], "carousel": True},
            cta(),
        ],
    }


S = lambda t, x: {"title": t, "text": x}

UC_PAGES = [
    usecase_page("site-selection", "Site selection", 'Rank sites by what people <span class="accent">can spend</span>.',
                 "Shortlist candidate pincodes by purchasing power, check each catchment, compare them side by side and save the winners.",
                 "ws-compare",
                 [S("Paint the city", "Colour by purchasing power, income or spend."), S("Shortlist", "Save candidate pincodes to a project."),
                  S("Compare", "Line up to five sites on the same metrics."), S("Decide", "Mark approved and send the report.")],
                 [Q("hero-blr-choropleth-ppi", "Find", "Spot the strong pockets", "The ranked list beside the map shows the top localities in view."),
                  Q("ws-compare", "Compare", "Weigh the shortlist", "Opportunity, income and spend side by side, with which site leads and by how much.")],
                 ["retail-fnb", "real-estate-hospitality", "any-business", "agencies-consultants"], ["Purchasing power", "Vehicles"]),
    usecase_page("market-sizing", "Market sizing", 'Size a market in <span class="accent">households and rupees</span>.',
                 "Estimate households and monthly spend for a city, a region or a radius, and see how it's spread across pincodes.",
                 "blr-heatmap-income",
                 [S("Choose the area", "A city, a radius around a point, or all of India."), S("Pick the measure", "Income, spend or purchasing power."),
                  S("Export", "Download by pincode for your model."), S("Segment", "Split the market by signal, for example car ownership.")],
                 [Q("blr-heatmap-income", "Spread", "Where the money sits", "A heatmap shows how income concentrates across a city."),
                  Q("map-explorer", "Segment", "Read it by signal", "Switch the map to car ownership, UPI value or tax filers to see who's behind the spend.")],
                 ["lending-fintech", "any-business", "agencies-consultants", "retail-fnb"], ["Purchasing power", "Tax & economy", "Banking & UPI"]),
    usecase_page("catchment-analysis", "Catchment analysis", 'Know who lives <span class="accent">around a site</span>.',
                 "Draw a ring around a site and read the purchasing power, income, spend and signals of every pincode inside it.",
                 "ws-map",
                 [S("Pick the site", "Click a pincode or drop a pin."), S("Set the radius", "A ring at the distance you choose."),
                  S("Read the catchment", "Scores and signals inside the ring."), S("Compare", "Against other sites or your best stores.")],
                 [Q("ws-map", "Read", "Intelligence on click", "Opportunity score, comparison with district and state, and the signals behind it."),
                  Q("ws-customer-data", "Benchmark", "Against your own stores", "See how a new catchment compares with the areas around your best stores.")],
                 ["retail-fnb", "real-estate-hospitality", "any-business"], ["Purchasing power", "Infrastructure", "Vehicles"]),
    usecase_page("credit-affordability", "Credit & affordability", 'An <span class="accent">area signal</span> for lending.',
                 "Use pincode-level income, spend and banking depth to prioritise sourcing and size products by region, alongside your own customer data.",
                 "del-choropleth-spend",
                 [S("Choose signals", "Income, spend, branches, deposits, UPI value."), S("Export by pincode", "CSV, Excel, JSON or API."),
                  S("Join to your book", "Match on pincode in your own systems."), S("Monitor", "Refreshed weekly; sources update on their own schedules.")],
                 [Q("del-choropleth-spend", "Affordability", "Spend by pincode", "Estimated household spend across Delhi NCR."),
                  Q("map-catalog", "Provenance", "Every signal sourced", "The catalog shows coverage and level for each signal.")],
                 ["lending-fintech", "agencies-consultants"], ["Purchasing power", "Banking & UPI", "Tax & economy"]),
    usecase_page("territory-planning", "Territory planning", 'Draw territories by <span class="accent">spending power</span>, not just area.',
                 "Balance sales, distribution and delivery territories so each has a comparable market behind it.",
                 "mum-cluster-ppi",
                 [S("See the spread", "Clusters show where value groups together."), S("Group pincodes", "Export and assign them to territories."),
                  S("Balance", "Compare total households and spend per territory."), S("Revisit", "Re-run as the data refreshes.")],
                 [Q("mum-cluster-ppi", "Clusters", "Where value groups", "Clusters merge nearby pincodes at low zoom."),
                  Q("del-choropleth-spend", "Balance", "Spend per area", "Use spend or purchasing power as the weight.")],
                 ["lending-fintech", "any-business", "retail-fnb"], ["Purchasing power", "Vehicles"]),
    usecase_page("portfolio-benchmarking", "Portfolio benchmarking", 'Score the stores <span class="accent">you already have</span>.',
                 "Upload your stores or branches with revenue and see each one against its area, so you know which are under- or over-performing their market.",
                 "ws-customer-data",
                 [S("Upload", "CSV or Excel with store, pincode and revenue."), S("Score", "Each store gets its area's scores."),
                  S("Find the pattern", "Which signals your best stores share."), S("Act", "Use the pattern to pick and size new sites.")],
                 [Q("ws-customer-data", "Your stores", "Each one scored", "Scores per store, from the area around it."),
                  Q("ws-forecast", "Calibrate", "A forecast that learns", "With three or more stores, the forecast calibrates to your own performance.")],
                 ["retail-fnb", "agencies-consultants", "lending-fintech"], ["Purchasing power", "Banking & UPI"]),
]

# ─────────────────────────────── Utility pages ───────────────────────────────

HOW = {
    "url": B + "how-it-works/", "og_image": "ws-dashboard",
    "title": "How PaisaMaps works: before and after you sign in",
    "description": "Use the map free without signing in, then sign in to save locations, unlock signals and plan "
                   "expansion in the workspace.",
    "sections": [
        {"type": "hero", "crumbs": crumbs(), "crumb_here": "How it works", "kicker": "How it works",
         "title": 'Start on the free map. <span class="accent">Decide</span> in the workspace.',
         "lede": "Everything on the map is open to anyone. Sign in when you want to save, unlock more signals, or plan with your team.",
         "ctas": [MAP, CONTACT], "shot": "ws-dashboard", "shot_alt": "The workspace dashboard"},
        {"type": "lanes", "kicker": "Two journeys", "title": "Before and after you sign in", "lanes": [
            {"title": "Before sign-in", "tag": "Free", "text": "For anyone curious about an area.", "steps": [
                {"title": "Open the map", "text": "No account needed."},
                {"title": "Search a city or pincode", "text": "Or let the map find where you are."},
                {"title": "Read the scores", "text": "Purchasing power, income, spend and the property-rate estimate."},
                {"title": "Switch views", "text": "Bubbles, choropleth, heatmap, clusters, bivariate, top-N."},
                {"title": "Sign in with Google", "text": "When you want to save locations."}],
             "ctas": [MAP]},
            {"title": "After sign-in", "tag": "Plans", "text": "For teams making a decision.", "steps": [
                {"title": "Unlock signals", "text": "Signals Lite (₹200) or Pro (₹500), or a dashboard plan."},
                {"title": "Create a project", "text": "Four short steps: business, data, signals, investment."},
                {"title": "Upload your stores", "text": "See each one scored and calibrate the forecast."},
                {"title": "Compare and forecast", "text": "Shortlist sites and turn a budget into a plan."},
                {"title": "Share", "text": "Invite your team, add free viewers, send a report."}],
             "ctas": [PRICING]},
        ]},
        {"type": "splits", "rows": [
            {"img": "mobile-mum-embed", "phone": True, "kicker": "Before", "title": "The map, anywhere",
             "text": "On a laptop or your phone, the free map shows the same scores."},
            {"img": "ws-wizard", "kicker": "After", "title": "Set up a project in minutes",
             "text": "The wizard saves as you go, so you can stop and pick up later."},
        ]},
        cta(),
    ],
}

WHY = {
    "url": B + "why-paisamaps/", "og_image": "ws-compare",
    "title": "Why PaisaMaps: pincode spending power, honestly built",
    "description": "What makes PaisaMaps different: pincode-level purchasing power for 15,331 pincodes, traceable "
                   "sources, decision tools and plain rupee pricing.",
    "sections": [
        {"type": "hero", "crumbs": crumbs(), "crumb_here": "Why PaisaMaps", "kicker": "Why PaisaMaps",
         "title": 'The fastest honest answer to <span class="accent">"where?"</span>',
         "lede": "Most teams piece this together from survey PDFs, spreadsheets and a consultant. PaisaMaps puts it on one map, at pincode level, ready to act on.",
         "ctas": [CONTACT, MAP], "shot": "hero-blr-choropleth-ppi", "shot_alt": "Bengaluru ranked by purchasing power"},
        {"type": "bento", "kicker": "What's different", "title": "Six reasons teams switch", "tiles": [
            {"cls": "green wide", "num": "15,331", "title": "pincodes, not districts", "text": "Decisions happen at street level. Most public data stops at the district."},
            {"cls": "", "num": "18", "title": "signals in one place", "text": "Banking, tax, vehicles, night-lights, UPI and more, already joined."},
            {"cls": "amber", "num": "₹0", "title": "to start", "text": "The map is free. Plans are monthly, priced in rupees, with GST invoices."},
            {"cls": "wide", "icon": "⇄", "title": "Decision tools, not just data", "text": "Compare, forecast, saved-location pipeline and reports, built for the moment you choose."},
            {"cls": "magenta", "icon": "✓", "title": "Honest labels", "text": "Modelled numbers are labelled. We say what we don't have."},
            {"cls": "", "icon": "🔒", "title": "Your data stays yours", "text": "Store data is encrypted and scoped to your company."},
        ]},
        {"type": "table", "kicker": "How it compares", "title": "PaisaMaps next to the usual options",
         "head": ["", "PaisaMaps", "Survey reports & PDFs", "Market-research study", "General GIS software"], "highlight": 0,
         "rows": [["Pincode-level spending power", "✓", "—", "Sometimes", "Bring your own data"],
                  ["Ready on day one", "✓", "✓", "Weeks", "Needs a GIS team"],
                  ["Compare and forecast sites", "✓", "—", "In the report", "Build it yourself"],
                  ["Refreshed", "Weekly", "Every few years", "Once", "As fresh as your data"],
                  ["Starting price", "Free map", "Free", "Priced per study", "Licence + team"]],
         "footnote": "General categories, not named products. Market research and GIS teams go deeper on footfall, panels and custom layers; many customers use PaisaMaps alongside them."},
        {"type": "html", "html": '<h2>What we don\'t do</h2><ul class="ticks"><li>No real-time, transaction-level or footfall data.</li><li>No individual-level credit scores. Our signals are area-level.</li><li>No OpenStreetMap-derived data in paid plans.</li><li>No property valuations. Property rate is a city-level estimate.</li></ul>'},
        cta(),
    ],
}


def pricing_page():
    T = P.TIERS
    dash = ["starter", "growth", "scale", "pro", "enterprise"]
    plans = [
        {"tag": "Free", "name": "Free map", "amount": "₹0", "per": "", "blurb": "The map and the core metrics.",
         "features": ["No sign-in to explore", "Save locations with a free account", "Property-rate estimate"]},
        {"tag": "Signals", "name": P.SIGNAL_TIERS["lite"]["label"], "amount": inr(P.SIGNAL_TIERS["lite"]["price_paise"]), "per": "/ month",
         "blurb": f"{len(P.SIGNAL_TIERS['lite']['extra_signals'])} more signals on the map.",
         "features": ["Banking, UPI, vehicles, tax", "One person", "Month to month"]},
        {"tag": "Signals", "name": P.SIGNAL_TIERS["pro"]["label"], "amount": inr(P.SIGNAL_TIERS["pro"]["price_paise"]), "per": "/ month",
         "blurb": "Every signal on the map.", "features": ["All signals", "One person", "Month to month"]},
        {"tag": "Try it", "name": "Trial", "amount": "₹0", "per": f"{P.TRIAL_DAYS} days",
         "blurb": "The whole workspace, no card.", "features": [f"{P.TRIAL_CREDITS} credits", f"{T['trial']['seats']} seats", "Drops to Free if you don't choose"]},
    ]
    head = ["", *[T[t]["label"] for t in dash]]
    fmt = lambda v, f=str: "Custom" if v is None else f(v)
    rows = [
        ["Monthly", *[("From " if t == "enterprise" else "") + inr(T[t]["price_paise"]) for t in dash]],
        ["Annual (per month)", *[fmt(T[t]["annual_monthly_paise"], inr) for t in dash]],
        ["Credits / month", *[f"{T[t]['credits_per_month']:,}" for t in dash]],
        ["Seats included", *[fmt(T[t]["seats"]) for t in dash]],
        ["Extra seat / month", *[fmt(T[t]["extra_seat_paise"], inr) for t in dash]],
        ["Companies", *[fmt(T[t]["companies"]) for t in dash]],
        ["Extra company / month", *["—" if T[t]["companies"] is None else fmt(T[t]["extra_company_paise"], inr) for t in dash]],
        ["API", *[{"rate_limited": "Higher limit", "full": "Full"}.get(T[t]["export_api"], "Standard") for t in dash]],
        ["Set up", *["Self-serve" if T[t]["self_serve"] else "With our team" for t in dash]],
    ]
    costs = P.CREDIT_COSTS
    topups = ", ".join(f"{v['label']} for {inr(v['price_paise'])}" for v in P.TOPUP_PACKS.values())
    return {
        "url": B + "pricing/", "og_image": "ws-dashboard",
        "title": "Pricing | PaisaMaps",
        "description": "Free map, signal plans from ₹200 a month, and dashboard plans from ₹5,000 a month with credits, "
                       "seats and free viewers. Prices in rupees, before GST.",
        "sections": [
            {"type": "head", "center": True, "kicker": "Pricing", "title": "Start free. Pay when it earns its keep.",
             "lede": f"All prices in rupees, before {P.GST_PERCENT}% GST. Monthly plans run month to month; annual plans save {int(P.ANNUAL_DISCOUNT*100)}% and add {int(P.ANNUAL_CREDIT_BONUS*100)}% bonus credits."},
            {"type": "pricing_teaser", "kicker": "Start here", "title": "Map and signals", "lede": "For one person reading the map.",
             "plans": plans, "footnote": "Signal plans don't include the workspace or credits.", "ctas": [MAP]},
            {"type": "table", "kicker": "Workspace plans", "title": "For teams planning expansion", "numeric": True, "highlight": 1,
             "head": head, "rows": rows,
             "footnote": "Viewers are free and don't use a seat. Seats are counted across your whole account.",
             "ctas": [CONTACT]},
            {"type": "bento", "kicker": "Credits", "title": "What credits pay for", "tiles": [
                {"cls": "green", "num": str(costs["forecast"]), "title": "credits per forecast", "text": "A full budget-to-sites forecast."},
                {"cls": "", "num": str(costs["expansion_recommend"]), "title": "credits per expansion plan", "text": "A recommended set of new sites."},
                {"cls": "amber", "num": str(costs["report_generate"]), "title": "credits per report", "text": "A PDF report for a project."},
                {"cls": "wide", "icon": "+", "title": "Top-ups", "text": f"{topups}. Top-up credits last {P.TOPUP_CREDIT_ROLLOVER_DAYS} days; plan credits roll over for {P.PLAN_CREDIT_ROLLOVER_DAYS} days."},
                {"cls": "magenta wide", "icon": "₹", "title": "Budgets", "text": "Cap what each company or person can spend, by billing cycle, month, week or quarter."},
            ]},
            {"type": "faq", "title": "Pricing questions", "items": [
                {"q": "Do prices include GST?", "a": f"No. GST at {P.GST_PERCENT}% is added at checkout, and every payment gets a GST invoice."},
                {"q": "How do I pay?", "a": "By card or UPI through Razorpay, with UPI Autopay for monthly plans. Scale and above are invoiced and set up with our team."},
                {"q": "Can I change plans?", "a": "Yes, move up any time. Moving from a signal plan to a dashboard plan replaces it."},
                {"q": "What happens when the trial ends?", "a": "If you haven't chosen a plan, your company drops to Free. You keep your saved locations."},
                {"q": "Do you refund?", "a": "See our refund policy for the details."},
            ], "more": {"label": "Refund policy", "url": "/refund"}},
            cta("Not sure which plan? Ask us", "Tell us how many people and cities, and we'll suggest the smallest plan that works."),
        ],
    }


FAQ = {
    "url": B + "faq/", "og_image": "map-catalog",
    "title": "FAQ | PaisaMaps",
    "description": "Answers about PaisaMaps' data, signals, map, workspace, pricing, security and accounts.",
    "sections": [
        {"type": "head", "center": True, "kicker": "FAQ", "title": "Questions, answered",
         "lede": "Can't find yours? Write to us and a person will reply."},
        {"type": "faq", "kicker": "Data", "title": "The data", "items": [
            {"q": "Where do the numbers come from?", "a": "Public sources: the national household spending survey, RBI banking data, income-tax filers, vehicle registrations, satellite night-lights, UPI value and more, joined to each pincode."},
            {"q": "Are they real incomes?", "a": "No. They're modelled estimates, good for comparing areas. They are not measurements of any household."},
            {"q": "How many pincodes do you cover?", "a": "15,331 with the core metrics, which is 80% of India Post's 19,097 pincodes. Coverage grows as people explore new areas."},
            {"q": "How often is it updated?", "a": "The model is refitted weekly and must pass fixed sanity checks before the map changes. Sources update on their own schedules."},
            {"q": "Do you have footfall or transaction data?", "a": "No. We don't claim real-time, transaction-level or footfall data."},
        ]},
        {"type": "faq", "kicker": "Product", "title": "The product", "items": [
            {"q": "What's free?", "a": "The map, the three core metrics and the property-rate estimate, without signing in. A free account lets you save locations."},
            {"q": "What's in the workspace?", "a": "Projects, the map workspace with location intelligence, compare, saved-location pipeline, store-data upload, forecast and reports."},
            {"q": "Can I use it on my phone?", "a": "The map works on phones. The workspace is designed for a laptop."},
            {"q": "Is there an API?", "a": "Yes. Any account can create an API key; Growth and above get higher limits."},
        ]},
        {"type": "faq", "kicker": "Security & privacy", "title": "Your data", "items": [
            {"q": "Who can see my store data?", "a": "Only people in your company. It's encrypted at rest and never shared with other customers."},
            {"q": "How do I sign in?", "a": "With your Google account. We don't store passwords."},
            {"q": "Where is my data stored?", "a": "The privacy policy sets out where and how we store and protect your data."},
        ]},
        {"type": "faq", "kicker": "Accounts & billing", "title": "Accounts & billing", "items": [
            {"q": "Do viewers cost anything?", "a": "No. Viewers are free and read-only: they don't use a seat or spend credits."},
            {"q": "Can one account pay for several companies?", "a": "Yes. Link companies to one paying account; usage is reported per company."},
            {"q": "Do I get a GST invoice?", "a": "Yes, for every payment."},
        ]},
        cta("Still have a question?", "Write to us and a person will reply."),
    ],
}

SUPPORT = {
    "url": B + "support/", "og_image": "ws-dashboard",
    "title": "Help & support | PaisaMaps",
    "description": "How to get help with PaisaMaps: email us, read the FAQ, or book a walkthrough.",
    "sections": [
        {"type": "head", "center": True, "kicker": "Help & support", "title": "Talk to a person",
         "lede": "PaisaMaps is a small team. Your message reaches the people who build it."},
        {"type": "cards", "kicker": "Ways to get help", "title": "Pick what fits", "cards": [
            {"url": B + "faq/", "title": "Read the FAQ", "text": "Data, product, pricing, security and accounts.", "tag": "Self-serve", "more": "Open FAQ"},
            {"url": B + "contact/", "title": "Send us a message", "text": "Questions, bugs or data requests. We reply on working days.", "tag": "Email", "more": "Write to us"},
            {"url": B + "contact/", "title": "Book a walkthrough", "text": "A 20-minute call on your cities and your decision.", "tag": "Call", "more": "Book a call"},
            {"url": B + "how-it-works/", "title": "How it works", "text": "Both journeys, before and after sign-in.", "tag": "Guide", "more": "Read the guide"},
            {"url": B + "product/signals/", "title": "Signals & data", "text": "Every signal, its source and its level.", "tag": "Guide", "more": "See the signals"},
            {"url": "/contact", "title": "Company details", "text": "Registered address and grievance officer.", "tag": "Legal", "more": "View details"},
        ]},
        {"type": "html", "html": '<p class="note">For account or billing problems, write from the email you sign in with and include your company name, so we can find your account quickly. Email: <b>ragho@paisamaps.com</b></p>'},
    ],
}

INTERESTS = [("retail-fnb", "Retail & F&B expansion"), ("real-estate-hospitality", "Real estate or hotels"),
             ("lending-fintech", "Lending or fintech"), ("any-business", "Growing my business"),
             ("agencies-consultants", "Work for clients (agency / consulting)"), ("api", "Data API or bulk data"),
             ("partnership", "Partnership"), ("other", "Something else")]

CONTACT_PAGE = {
    "url": B + "contact/", "og_image": "ws-compare",
    "title": "Talk to us | PaisaMaps",
    "description": "Book a 20-minute walkthrough of PaisaMaps on your own cities, or ask about plans, data and the API.",
    "sections": [
        {"type": "head", "kicker": "Talk to us", "title": "See your market on the map with us",
         "lede": "Tell us the cities and the decision you're making. We'll reply on working days, usually with a time for a 20-minute walkthrough."},
        {"type": "contact", "interests": INTERESTS, "side": [
            {"tag": "Email", "title": "ragho@paisamaps.com", "text": "If you'd rather write directly."},
            {"tag": "Try first", "title": "The free map", "text": 'No sign-in needed. <a href="/">Open the map</a>.'},
            {"tag": "Plans", "title": "Pricing", "text": f'Signal plans from ₹200, workspace from ₹5,000 a month. <a href="{B}pricing/">See pricing</a>.'},
        ]},
    ],
}

ALL = [PRODUCT, MAP_PAGE, DASHBOARD, SIGNALS_PAGE, FORECAST, API, SOLUTIONS_HUB,
       RETAIL, REAL_ESTATE, LENDING, ANY, AGENCIES, *UC_PAGES, HOW, WHY, pricing_page(), FAQ, SUPPORT, CONTACT_PAGE]
