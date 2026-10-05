# Data licensing review

**Status: first-pass inventory, not legal clearance.** This lists every external
data source PaisaMap ingests or proxies, its licence as best understood from the
fetch scripts and source portals, and whether it looks safe to use in a
**commercially sold** product (the API/tiered-access business in the growth
plan). Anything marked ⚠️ or 🔴 needs a real read of the source's terms — and,
before selling, a lawyer — not this file.

Last reviewed: 2026-10-05. Re-check whenever a new `fetch_*.py` is added.

---

## Summary

| Risk | Sources | Why |
|---|---|---|
| 🟢 Low | Most `data.gov.in` / OGD datasets, PhonePe Pulse, NASA VIIRS, CBDT/MOSPI/ASI government reports | Government Open Data Licence – India (GODL) or an explicit permissive licence; raw facts, not copyrightable compilations. Commercial reuse allowed with attribution. |
| ⚠️ Medium | RBI publications (Handbook of Statistics, BSR), UDISE+ bulk export, VAHAN/Parivahan | Government data but the portal's own terms restrict or are silent on commercial redistribution; or the acquisition method (scrape) is a separate problem from the licence. |
| 🔴 High | OpenStreetMap-derived data — `poi_density` (Overpass) and Nominatim **forward** search (`/api/search`, customer-upload geocoding) | **ODbL share-alike.** A database that includes OSM-derived data, sold or made public, may itself have to be ODbL. Nominatim also has a usage policy our server-side search proxy likely breaches at scale. Reverse geocoding (lat/lng → PIN) no longer uses Nominatim; it uses the DoP polygons below. |

**The single biggest issue: OSM / ODbL.** `premium_poi_per_km2` is a feature in
the ML ensemble and an exported column. If it's a "Derivative Database" of OSM
under ODbL, distributing it (even inside a paid API) can trigger the share-alike
obligation. Options, in rough order of effort: (a) drop the POI signal from any
commercially sold tier; (b) replace it with a non-OSM POI source; (c) take
proper ODbL advice on whether our aggregation (counts per pincode, folded into a
model) is a "Produced Work" rather than a Derivative Database — the produced-work
carve-out only requires attribution, not share-alike. Do not sell the POI column
until this is settled.

---

## Source-by-source

### Core PPI / income / spend

| Source | Used for | Licence | Commercial? | Notes |
|---|---|---|---|---|
| NSSO **HCES 2023-24** MPCE (via a GitHub mirror) | `mpce_combined`, the PPI base | Government survey data; the mirror repo has no separate licence | ⚠️ likely OK (government facts) | Verify the mirror isn't adding restrictions; ideally pull from the official MoSPI release. |
| **Karnataka DES** district/taluk income (`data.gov.in`, AIKosh) | Karnataka income calibration | GODL-India | 🟢 yes, with attribution | Fetched via `api.data.gov.in` with the published sample key. |
| **India Post** All-India Pincode Directory (`data.gov.in`) | office names per pincode | GODL-India | 🟢 yes, with attribution | |
| **Dept. of Posts** PIN code boundary dataset (`data.gov.in`, NDSAP) | `boundaries.geojson`, and reverse geocoding (`/api/reverse`, `pin_locate.py`, `detect_district`, `batch_enrich_hces.reverse_pincode`) | GODL-India / NDSAP | 🟢 yes, with attribution | Committed pre-simplified copy at `paisamap-etl/data/reference/pincode_boundary_master/india_pincode_boundaries_simplified.geojson` (source file on data.gov.in, All-India PIN boundary, simplified for the app). Attribute "Department of Posts, Government of India". District, state, and office name joined from the India Post directory CSVs, not from OSM. A point outside every polygon snaps to the nearest boundary within 1 km or returns no pincode — never a synthetic id. |

### Signal columns

| Column(s) | Source | Licence | Commercial? | Notes |
|---|---|---|---|---|
| `msme_per_lakh` | MSME Udyam district counts (`data.gov.in`, OGD resource) | GODL-India | 🟢 yes | Live-fetchable via `curl`. |
| `factories_per_lakh` | ASI state factory counts — Rajya Sabha Unstarred Q 2397, via `data.gov.in` | GODL-India | 🟢 yes | Parliamentary answer + OGD resource. |
| `filers_per_capita` | CBDT Annual Report 2022-23, state-wise taxpayer distribution | Government report, published & widely cited | 🟢 likely yes | Facts from an official publication; attribute CBDT. |
| `nsdp_per_capita` | **RBI** Handbook of Statistics on Indian States, Table 19 | RBI © — site terms permit reproduction *with acknowledgement*; commercial redistribution not explicitly granted | ⚠️ verify | RBI's website terms are the thing to read. Underlying NSDP figures are MoSPI/state-DES facts. |
| `bank_branches_per_lakh`, `deposits_per_capita`, `credit_deposit_ratio` | **RBI** BSR-1/BSR-2 + branch master | Same as above — RBI publications | ⚠️ verify | Same RBI-terms question. |
| `schools_per_lakh` | **UDISE+** school-level bulk export (Ministry of Education) | Portal terms unclear for bulk redistribution | ⚠️ verify | Bulk-export feature exists but ToU for commercial reuse of the export isn't spelled out. |
| `cropping_intensity_pct` | **MOSPI** Statistical Year Book, Table 8.1 | Government publication | ⚠️ verify + 🕰️ **stale (2012-13 data)** | Licence is likely fine; the vintage is the real problem — flag to customers or refresh. |
| `cars_per_1000`, `lmv_per_1000`, `luxury_share`, `ev_share`, `car_2w_ratio` | **VAHAN/Parivahan** dashboard AJAX + MoRTH annual report fallback | Government data; VAHAN dashboard has no explicit open licence, AJAX scrape is grey | ⚠️ acquisition risk | The MoRTH annual-report fallback is a cleaner provenance than the live scrape — prefer it for anything sold. |
| `upi_txn_value_per_capita` (district only) | **PhonePe Pulse** GitHub repo (`github.com/PhonePe/pulse`) | **CDLA-Permissive-2.0** | 🟢 yes | Explicitly permits commercial use, no attribution-of-derived-data obligation. Applies ONLY to the GitHub repo data. |
| ~~`upi_txn_count_nearby`~~ (removed 2026-09-29) | PhonePe Pulse **live website** JSON (`phonepe.com/pulsestatic/…`) — NOT the GitHub repo | phonepe.com site terms: no commercial use, no automated extraction, no combining with other datasets | 🔴 **removed** | Was wrongly filed under the CDLA row above. Removed from code, the public map and `broad_coverage.csv`; the CSV is quarantined outside the repo. Still present in the public repo's git history — founder decision (private repo vs history rewrite). |
| `radiance_mean` | **NASA VIIRS** black-marble via AppEEARS | US Government work — public domain | 🟢 yes | Free NASA Earthdata account needed to fetch, not to redistribute. |
| `premium_poi_per_km2` | **OSM** via Overpass API | **ODbL** (share-alike) | 🔴 **do not sell until resolved** | See the ODbL note above. |
| `rate_per_sqft` | **PaisaMap's own estimate** — `enrich_single.py` scales a hardcoded per-city prior (`CITY_PRIORS`) by a local ratio; `llm_extract.py` also pulls transaction value/area from documents | our own modelled value | 🟢 likely OK | Not a scraped feed. Residual question: where the `CITY_PRIORS` numbers originally came from (manual research vs. a copied table). Document that provenance; if any prior was lifted from a portal's published "average rate" table, note it. |

### Services (not stored, called at request time)

| Service | Used for | Terms | Notes |
|---|---|---|---|
| **Nominatim** (OSM) — `/api/search`, `etl/_geocode.py` (customer-upload forward search), and `batch_enrich_hces.geocode_district` | address / district-name **forward** search | OSM data = ODbL; **Nominatim Usage Policy**: max ~1 req/s, no bulk, must allow caching, requires a real `User-Agent` (we send `PaisaMap-Server/1.0`) | 🔴 Forward search still funnels lookups through one server IP. `/api/reverse` no longer calls Nominatim (DoP polygons, row above). Replace or self-host the forward geocoder before scaling search; do not send reverse traffic back to the public instance. |
| **Esri** Light Gray basemap + ArcGIS | map tiles / basemap | Esri ToU — the key is referrer-locked | Basemap display is within normal ToU; don't extract or resell tiles. |

---

## Attribution block (draft — put on the site + in API docs once cleared)

> Contains data from: Government Open Data Platform India (data.gov.in) under
> the Government Open Data Licence – India; Department of Posts, Government of
> India; Reserve Bank of India; Ministry of Statistics and Programme
> Implementation; Central Board of Direct Taxes; PhonePe Pulse
> (CDLA-Permissive-2.0); NASA VIIRS (public domain); © OpenStreetMap
> contributors (ODbL). Derived signals and the Purchasing Power Index are
> PaisaMap's own work.

---

## Action items (also mirrored in `TODO.md`)

1. Document where `CITY_PRIORS` (the seed for `rate_per_sqft`) came from — it's
   our own estimate, but note any prior copied from a portal's published table.
2. 🔴 **Resolve the OSM/ODbL question for `premium_poi_per_km2`** — legal advice
   on produced-work vs derivative-database, or drop/replace the column.
3. 🔴 **Nominatim forward search** (`/api/search`, `_geocode.py`,
   `batch_enrich_hces.geocode_district`): still the public instance. Reverse
   geocoding moved to DoP / NDSAP polygons (2026-10-05). Plan a self-hosted or
   paid geocoder for forward search before that API surface scales. The
   in-repo rate limit on `/api/search` stays; production nginx's `geo` zone
   still names `/api/reverse` and should drop it now that reverse is local.
4. ⚠️ **Read RBI's website terms** for the Handbook / BSR data and get a
   yes/no on commercial redistribution of derived figures.
5. ⚠️ **Read UDISE+ terms** for bulk-export redistribution.
6. ⚠️ Switch the vehicle signals to the **MoRTH annual-report** provenance, not
   the live VAHAN scrape, for anything sold.
7. 🕰️ Refresh or footnote `cropping_intensity_pct` (2012-13).
8. Publish the attribution block once 1–5 are cleared.
