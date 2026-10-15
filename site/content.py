"""Copy and structure for the business site (/business/). See docs/BUSINESS_SITE_IA.md.

Honesty rules: numbers come from the data or the price book; scores are modelled
estimates; no invented customers, logos or testimonials.
"""

B = "/business/"

NAV = [
    {"label": "Product", "groups": [
        {"label": "Platform", "links": [
            {"label": "Platform overview", "url": B + "product/", "blurb": "Map, signals, workspace and API in one tour"},
            {"label": "Live map", "url": B + "product/map/", "blurb": "Spending power for every pincode, six ways to see it"},
            {"label": "Workspace", "url": B + "product/dashboard/", "blurb": "Projects, store data, compare, reports"},
        ]},
        {"label": "Data & tools", "links": [
            {"label": "Signals & data", "url": B + "product/signals/", "blurb": "What we capture, from where, how often"},
            {"label": "Expansion forecast", "url": B + "product/forecast/", "blurb": "From a budget to ranked sites and revenue ranges"},
            {"label": "Data API & exports", "url": B + "product/api/", "blurb": "CSV, Excel, JSON and API keys"},
        ]},
    ]},
    {"label": "Solutions", "groups": [
        {"label": "By industry", "links": [
            {"label": "Retail & F&B", "url": B + "solutions/retail-fnb/", "blurb": "Where to open the next store"},
            {"label": "Real estate & hotels", "url": B + "solutions/real-estate-hospitality/", "blurb": "Catchment demand for projects and properties"},
            {"label": "Lenders, NBFC & fintech", "url": B + "solutions/lending-fintech/", "blurb": "Area-level affordability and market size"},
            {"label": "Any business", "url": B + "solutions/any-business/", "blurb": "D2C, franchises, services, SMEs"},
            {"label": "Agencies & consultants", "url": B + "solutions/agencies-consultants/", "blurb": "Strategy firms working for many clients"},
        ]},
        {"label": "By use case", "links": [
            {"label": "Site selection", "url": B + "use-cases/site-selection/", "blurb": "Rank candidate pincodes"},
            {"label": "Market sizing", "url": B + "use-cases/market-sizing/", "blurb": "Households × spend for any area"},
            {"label": "Catchment analysis", "url": B + "use-cases/catchment-analysis/", "blurb": "Who lives around a site"},
            {"label": "Credit & affordability", "url": B + "use-cases/credit-affordability/", "blurb": "An area input for lending"},
            {"label": "Territory planning", "url": B + "use-cases/territory-planning/", "blurb": "Balance sales territories"},
            {"label": "Portfolio benchmarking", "url": B + "use-cases/portfolio-benchmarking/", "blurb": "Score the stores you already have"},
        ]},
    ]},
    {"label": "Pricing", "url": B + "pricing/"},
    {"label": "Resources", "groups": [
        {"label": "Learn", "links": [
            {"label": "How it works", "url": B + "how-it-works/", "blurb": "Before and after you sign in"},
            {"label": "Why PaisaMaps", "url": B + "why-paisamaps/", "blurb": "What's different, honestly compared"},
        ]},
        {"label": "Help", "links": [
            {"label": "FAQ", "url": B + "faq/", "blurb": "Product, data, pricing, security"},
            {"label": "Help & support", "url": B + "support/", "blurb": "Get an answer from a person"},
        ]},
    ]},
]

FOOTER = [
    {"label": "Product", "links": [
        {"label": "Platform overview", "url": B + "product/"}, {"label": "Live map", "url": B + "product/map/"},
        {"label": "Workspace", "url": B + "product/dashboard/"}, {"label": "Signals & data", "url": B + "product/signals/"},
        {"label": "Pricing", "url": B + "pricing/"}]},
    {"label": "Solutions", "links": [
        {"label": "Retail & F&B", "url": B + "solutions/retail-fnb/"},
        {"label": "Real estate & hotels", "url": B + "solutions/real-estate-hospitality/"},
        {"label": "Lenders & fintech", "url": B + "solutions/lending-fintech/"},
        {"label": "Any business", "url": B + "solutions/any-business/"},
        {"label": "Agencies & consultants", "url": B + "solutions/agencies-consultants/"}]},
    {"label": "Use cases", "links": [
        {"label": "Site selection", "url": B + "use-cases/site-selection/"},
        {"label": "Market sizing", "url": B + "use-cases/market-sizing/"},
        {"label": "Catchment analysis", "url": B + "use-cases/catchment-analysis/"},
        {"label": "Territory planning", "url": B + "use-cases/territory-planning/"}]},
    {"label": "Company", "links": [
        {"label": "Talk to us", "url": B + "contact/"}, {"label": "Help & support", "url": B + "support/"},
        {"label": "FAQ", "url": B + "faq/"}, {"label": "Open the map", "url": "/"}]},
]

FACTS = {"pincodes": "15,331", "coverage": "80%", "india_post": "19,097", "signals": "15", "core": "3"}

SOURCES = [
    {"kind": "Survey", "label": "NSSO HCES household spending"},
    {"kind": "Banking", "label": "RBI branches & deposits"},
    {"kind": "Tax", "label": "CBDT income-tax filers"},
    {"kind": "Satellite", "label": "NASA VIIRS night-lights"},
    {"kind": "Payments", "label": "PhonePe Pulse UPI value"},
    {"kind": "Vehicles", "label": "VAHAN registrations: cars, EVs, luxury"},
    {"kind": "Business", "label": "MSME Udyam registrations"},
    {"kind": "Industry", "label": "ASI factory counts"},
    {"kind": "Education", "label": "UDISE+ schools"},
    {"kind": "Economy", "label": "State NSDP per capita"},
    {"kind": "Boundaries", "label": "India Post PIN polygons"},
    {"kind": "Income", "label": "Karnataka DES district income"},
]

# Signal palette: group → ramp (the map's own colours, index.html PALETTES /
# SIGNAL_GROUP_PALETTES) and, per signal, what it tells you, its geographic
# level and source. Tiers are read from the code at build time.
SIGNAL_GROUPS = [
    {"label": "Purchasing power", "ramp": ["#D73027", "#FC8D59", "#FEE08B", "#D9EF8B", "#91CF60", "#1A9850"], "signals": [
        {"key": "ppi_ml", "label": "Purchasing Power Index", "what": "One number for an area's spending power, 100 = the regional average.", "level": "Pincode · modelled", "source": "Built from every signal below"},
        {"key": "est_monthly_income_hh", "label": "Household income / month", "what": "Estimated monthly income of a typical household in the pincode.", "level": "Pincode · modelled", "source": "NSSO HCES, calibrated with state and district data"},
        {"key": "est_monthly_spend_hh", "label": "Household spend / month", "what": "Estimated monthly consumption spend of a typical household.", "level": "Pincode · modelled", "source": "NSSO HCES household spending survey"},
    ]},
    {"label": "Banking & UPI", "ramp": ["#B2182B", "#EF8A62", "#FDDBC7", "#D1E5F0", "#67A9CF", "#2166AC"], "signals": [
        {"key": "bank_branches_per_lakh", "label": "Bank branches / lakh people", "what": "How well served an area is by formal banking.", "level": "District", "source": "RBI branch statistics"},
        {"key": "deposits_per_capita", "label": "Bank deposits per person", "what": "Savings held in banks, a proxy for accumulated wealth.", "level": "District", "source": "RBI BSR deposits"},
        {"key": "upi_txn_value_per_capita", "label": "UPI value per person", "what": "How much people pay digitally, a live signal of everyday spending.", "level": "District", "source": "PhonePe Pulse"},
    ]},
    {"label": "Tax & economy", "ramp": ["#B35806", "#F1A340", "#FEE0B6", "#D8DAEB", "#998EC3", "#542788"], "signals": [
        {"key": "filers_per_capita", "label": "Income-tax filers per person", "what": "Share of people filing returns, a direct marker of formal income.", "level": "State", "source": "CBDT annual report"},
        {"key": "msme_per_lakh", "label": "MSMEs / lakh people", "what": "Density of small businesses, a read on local enterprise.", "level": "District", "source": "MSME Udyam registrations"},
        {"key": "factories_per_lakh", "label": "Factories / lakh people", "what": "Industrial employment base.", "level": "State", "source": "ASI factory counts"},
        {"key": "nsdp_per_capita", "label": "State output per person", "what": "Net state domestic product per head, the wider economy around the area.", "level": "State", "source": "State NSDP statistics"},
        {"key": "cropping_intensity_pct", "label": "Cropping intensity", "what": "How intensively farmland is used, useful for rural markets.", "level": "State", "source": "MoSPI statistical year book"},
    ]},
    {"label": "Infrastructure", "ramp": ["#D73027", "#FC8D59", "#FEE090", "#E0F3F8", "#91BFDB", "#4575B4"], "signals": [
        {"key": "radiance_mean", "label": "Night-light brightness", "what": "Satellite-measured light at night, a strong proxy for economic activity.", "level": "Pincode", "source": "NASA VIIRS"},
        {"key": "schools_per_lakh", "label": "Schools / lakh people", "what": "Social infrastructure and family density.", "level": "District", "source": "UDISE+"},
    ]},
    {"label": "Vehicles", "ramp": ["#00695C", "#4DB6AC", "#B2DFDB", "#F5C6CB", "#E57373", "#C62828"], "signals": [
        {"key": "lmv_per_1000", "label": "Cars / 1,000 people", "what": "Car ownership, one of the clearest affluence signals.", "level": "District", "source": "VAHAN registrations"},
        {"key": "car_2w_ratio", "label": "Car to two-wheeler ratio", "what": "How far an area has moved up from two-wheelers.", "level": "District", "source": "VAHAN registrations"},
        {"key": "luxury_share", "label": "Luxury vehicle share", "what": "Share of premium vehicles among registrations.", "level": "District", "source": "VAHAN registrations"},
        {"key": "ev_share", "label": "EV share", "what": "Early-adopter and premium-buyer signal.", "level": "District", "source": "VAHAN registrations"},
    ]},
    {"label": "Property", "ramp": ["#C51B7D", "#E9A3C9", "#FDE0EF", "#E6F5D0", "#A1D76A", "#4D9221"], "signals": [
        {"key": "rate_per_sqft", "label": "Property rate / sq ft", "what": "Indicative price of property. A city-level estimate, not transaction data.", "level": "City-level estimate", "source": "PaisaMaps estimate"},
    ]},
]

RAMPS = [
    {"label": "Purchasing power", "colors": SIGNAL_GROUPS[0]["ramp"], "low": "below the region", "high": "above the region"},
    {"label": "Household income", "colors": ["#762A83", "#AF8DC3", "#E7D4E8", "#D9F0D3", "#7FBF7B", "#1B7837"], "low": "lower", "high": "higher"},
    {"label": "Household spend", "colors": ["#8C510A", "#D8B365", "#F6E8C3", "#C7EAE5", "#5AB4AC", "#01665E"], "low": "lower", "high": "higher"},
    {"label": "Project suitability", "colors": ["#D53E4F", "#FC8D59", "#FEE08B", "#E6F598", "#99D594", "#3288BD"], "low": "poor fit", "high": "strong fit"},
]

HOME = {
    "url": B,
    "title": "PaisaMaps for business: spending power for every Indian pincode",
    "description": "Find where your customers can spend. PaisaMaps scores 15,331 Indian pincodes on income, spend "
                   "and 15 economic signals, on a live map and in a workspace built for expansion decisions.",
    "sections": [
        {"type": "hero", "kicker": "PaisaMaps for business",
         "title": 'Know what every pincode <span class="accent">can spend</span> before you open there.',
         "lede": "Purchasing power, household income and spend for 15,331 Indian pincodes, scored from public data "
                 "and refreshed weekly. See it on the map, then plan expansion in a workspace your team shares.",
         "ctas": [{"label": "Talk to us", "url": B + "contact/"}, {"label": "Open the free map", "url": "/"}],
         "meta": ["Free map, no sign-in", "80% of India Post pincodes", "Prices in ₹, GST invoices"],
         "shots": [
             {"img": "mum-choropleth-ppi", "title": "Mumbai", "caption": "purchasing power by pincode"},
             {"img": "blr-bubble-ppi", "title": "Bengaluru", "caption": "purchasing power, bubbles"},
             {"img": "mum-choropleth-income", "title": "Mumbai", "caption": "estimated household income"},
             {"img": "del-choropleth-spend", "title": "Delhi NCR", "caption": "estimated household spend"},
             {"img": "hyd-heatmap-ppi", "title": "Hyderabad", "caption": "purchasing power heatmap"},
         ]},

        {"type": "tabs", "kicker": "Built for the people who decide where",
         "title": "One map, five ways to use it",
         "lede": "Start with the industry closest to yours. Each has its own page with the views and signals it uses most.",
         "tabs": [
             {"label": "Retail & F&B", "kicker": "Lead use: site selection",
              "title": "Where does store #40 go?",
              "text": "Rank candidate pincodes by spending power, see the catchment around each site, and compare them "
                      "side by side before you sign a lease.",
              "bullets": ["Purchasing power, income and spend for every pincode in a city",
                          "Upload your own stores and see how each one's area scores",
                          "A forecast that turns a budget into ranked sites and a revenue range"],
              "img": "hero-blr-choropleth-ppi",
              "ctas": [{"label": "Retail & F&B", "url": B + "solutions/retail-fnb/"}]},
             {"label": "Real estate & hotels", "kicker": "Catchment demand",
              "title": "Is there demand around this project?",
              "text": "Read the spending power of the pincodes around a project, a hotel site or a mall, and how it "
                      "compares with the rest of the city.",
              "bullets": ["Heatmaps of income and spend across a city",
                          "Catchment rings at the radius you choose",
                          "Shortlists you can share with investors and partners"],
              "img": "mum-choropleth-income",
              "ctas": [{"label": "Real estate & hotels", "url": B + "solutions/real-estate-hospitality/"}]},
             {"label": "Lenders & fintech", "kicker": "Area affordability",
              "title": "How much can this area afford?",
              "text": "Use pincode-level income, spend and banking depth as an area input for sourcing, limits and "
                      "market sizing, alongside your own bureau data.",
              "bullets": ["Banking branches, deposits and UPI value per head",
                          "Bulk export by pincode for your models",
                          "API keys with rate limits by plan"],
              "img": "del-choropleth-spend",
              "ctas": [{"label": "Lenders, NBFC & fintech", "url": B + "solutions/lending-fintech/"}]},
             {"label": "Any business", "kicker": "Know your market",
              "title": "Where are the customers who can afford you?",
              "text": "D2C brands, franchises and service businesses use the same map to pick cities, plan delivery "
                      "zones and size a new market, without a GIS team.",
              "bullets": ["Free map, no sign-in needed",
                          "Signals you can switch on one at a time",
                          "Plans that start small and grow with you"],
              "img": "pune-choropleth-ppi",
              "ctas": [{"label": "Any business", "url": B + "solutions/any-business/"}]},
             {"label": "Agencies & consultants", "kicker": "For many clients",
              "title": "One account, every client",
              "text": "Strategy firms, agencies and consultants run each client as its own company, paid from one "
                      "wallet, with usage reported per client.",
              "bullets": ["Separate companies per client, one bill",
                          "Budgets and allowances per client and per person",
                          "Exports and reports you can hand over"],
              "img": "mum-cluster-ppi",
              "ctas": [{"label": "Agencies & consultants", "url": B + "solutions/agencies-consultants/"}]},
         ]},

        {"type": "bento", "kicker": "What you get", "title": "Everything to decide where, in one place",
         "tiles": [
             {"cls": "green wide", "num": FACTS["pincodes"], "title": "pincodes scored",
              "text": f"That's {FACTS['coverage']} of India Post's {FACTS['india_post']} pincodes, and the number grows as people explore new areas."},
             {"cls": "", "num": FACTS["core"] + " + " + FACTS["signals"], "title": "core metrics + signals",
              "text": "Purchasing power, income and spend, plus banking, tax, vehicles, night-lights, UPI and more."},
             {"cls": "amber", "num": "Weekly", "title": "refresh",
              "text": "The model is refitted every week and checked against fixed sanity gates before it goes live."},
             {"cls": "wide tall", "title": "Six ways to see the same city",
              "text": "Bubbles, choropleth, heatmap, clusters, bivariate and top-N, so you can read a city however you think.",
              "img": "blr-bubble-ppi"},
             {"cls": "magenta", "icon": "⇅", "title": "Your stores, scored",
              "text": "Upload a CSV or Excel file of your stores and see each one against its area."},
             {"cls": "", "icon": "₹", "title": "Budget → sites",
              "text": "The forecast turns an investment budget into ranked sites with a revenue range."},
             {"cls": "wide", "icon": "⤓", "title": "Reports, exports and an API",
              "text": "PDF reports for the board, CSV/Excel/JSON exports for analysts, and API keys for your systems."},
         ]},

        {"type": "compare", "kicker": "Read the same city two ways",
         "title": "Drag to compare income and spending power",
         "lede": "Every view is one click away on the map. Here's the same Mumbai view by estimated income and by purchasing power.",
         "bullets": ["Colours are ranked within the region, so local differences stand out",
                     "Click any pincode for its numbers and signals",
                     "Switch views without losing your place"],
         "left": "mum-choropleth-income", "left_label": "Income",
         "right": "mum-choropleth-ppi", "right_label": "Purchasing power"},

        {"type": "carousel", "kicker": "The map", "title": "See a city the way your business needs",
         "lede": "These are real screens from the live map, the same one anyone can open without signing in.",
         "shots": [
             {"img": "blr-bubble-ppi", "title": "Bubbles", "caption": "size shows the value, Bengaluru"},
             {"img": "blr-heatmap-income", "title": "Heatmap", "caption": "income density, Bengaluru"},
             {"img": "mum-cluster-ppi", "title": "Clusters", "caption": "grouped at low zoom, Mumbai"},
             {"img": "blr-bivariate", "title": "Bivariate", "caption": "two signals at once, Bengaluru"},
             {"img": "full-mum-app", "title": "Full app", "caption": "ranked localities beside the map, Mumbai"},
         ]},

        {"type": "steps", "kicker": "How it works", "title": "From a free look to a team workspace",
         "steps": [
             {"title": "Explore free", "text": "Open the map, search any city or pincode and read its purchasing power. No sign-in."},
             {"title": "Sign in", "text": "Save locations and unlock more signals with a free account or a ₹200/month signal plan."},
             {"title": "Plan in the workspace", "text": "Create a project, upload your stores, compare sites and run the forecast."},
             {"title": "Share and decide", "text": "Invite your team, export the data and send a PDF report to whoever signs off."},
         ],
         "ctas": [{"label": "See both journeys", "url": B + "how-it-works/"}]},

        {"type": "marquee", "kicker": "Signal capturing", "title": "Built on public data you can trace",
         "items": SOURCES,
         "note": "Scores are modelled estimates built from these sources, not measured household incomes. Each signal's "
                 "source and refresh cadence is listed on the Signals & data page."},

        {"type": "pricing_teaser", "kicker": "Pricing", "title": "Start free. Pay when it earns its keep.",
         "lede": "All prices in rupees, before GST. The full table, credits and seat details are on the pricing page.",
         "plans": [],  # filled from the price book by build.py
         "footnote": "Monthly prices shown. Annual plans save 20%. Scale and above are set up with our team.",
         "ctas": [{"label": "See full pricing", "url": B + "pricing/"}, {"label": "Talk to us", "url": B + "contact/"}]},

        {"type": "faq", "title": "Questions buyers ask first",
         "items": [
             {"q": "Where do the numbers come from?",
              "a": "From public sources: the national household spending survey, RBI banking data, income-tax filer "
                   "counts, vehicle registrations, satellite night-lights, UPI value and more. We combine them into a "
                   "purchasing-power score and income and spend estimates for each pincode. They are modelled "
                   "estimates, not measured incomes."},
             {"q": "How current is the data?",
              "a": "The model is refitted weekly and has to pass fixed sanity checks before anything changes on the "
                   "map. Each source updates on its own schedule, which the Signals & data page lists."},
             {"q": "Can I try it before paying?",
              "a": "Yes. The map is free without signing in. A free account lets you save locations, and the dashboard "
                   "comes with a 7-day trial that needs no card."},
             {"q": "Can I use my own store data?",
              "a": "Yes. Upload a CSV or Excel file of your stores in the workspace; we geocode them and score each one "
                   "against its area. Your data stays in your company's account and is encrypted at rest."},
             {"q": "Do you have an API?",
              "a": "Yes. Any account can create an API key for pincode-level exports; Growth plans and above get higher rate limits."},
         ],
         "more": {"label": "All FAQs", "url": B + "faq/"}},

        {"type": "cta", "title": "See your market on the map in a 20-minute call",
         "lede": "Tell us the cities and the kind of site you're looking for, and we'll walk through them with you.",
         "ctas": [{"label": "Talk to us", "url": B + "contact/"}, {"label": "Open the free map", "url": "/"}]},
    ],
}

from pages import ALL as _MORE  # noqa: E402
PAGES = [HOME] + _MORE
