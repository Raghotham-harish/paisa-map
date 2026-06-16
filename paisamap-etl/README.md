# PaisaMap — Phase-0 ETL & Composite PPI

Builds a defensible Purchasing Power Index (PPI) per **pin code** from free,
public proxy datasets. No individual-level data anywhere (DPDP-safe by design).

## The formula

For each pin code *p* and proxy *i*:

```
z_i(p)   = winsorized z-score of proxy i across all pin codes in the region
composite(p) = Σ  w_i · z_i(p)
PPI(p)   = clip( 100 + 30 · composite(p),  40, 200 )      # 100 = regional mean
```

Estimated household income & spend are anchored to the HCES urban MPCE baseline
and scaled by the composite (see `etl/pipeline.py::estimate_income_spend`).

## Proxy weights (v0 — tune after validation)

| # | Proxy | Weight | Granularity | Source |
|---|-------|--------|-------------|--------|
| 1 | Property rate (₹/sq ft) | 0.25 | pin code | Portal partnership / sampled listings |
| 2 | Bank deposits per capita | 0.25 | district → downscaled | RBI Quarterly BSR, data.rbi.org.in (DBIE) |
| 3 | Car ownership per 1,000 | 0.15 | RTO → mapped | VAHAN dashboard, vahan.parivahan.gov.in |
| 4 | Night-time lights radiance | 0.15 | ~500 m grid → zonal mean | NASA/NOAA VIIRS, eogdata.mines.edu |
| 5 | ITR filers per capita | 0.10 | district/city | incometaxindia.gov.in statistics |
| 6 | Premium POI density | 0.10 | pin code | OSM Overpass (malls, banks, premium retail) |

Spend baseline: **HCES 2023-24** urban MPCE fact sheets — mospi.gov.in.

## Geometry (the boundary problem, solved)

- `datameet/PincodeBoundary` — GeoJSON pin-code polygons (CC BY-SA 2.5 IN)
- `data014/India-Shapefiles-Bundle` — all-India pincode boundary GeoJSON bundle
- Districts (for downscaling district-keyed sources): `datameet/maps` (CC BY 4.0)

Downscaling rule: district-level values are assigned to member pin codes
weighted by pin-code population share (WorldPop raster zonal sums, or census
ward proxies). v0 ships with uniform share + property-rate modulation.

## Validation gates (must pass before anything ships)

1. **Sanity ordering** — Golf Links pin (110003) > Saket (110017) > Narela (110040).
2. **Rank correlation** vs known affluence ordering of ~30 NCR pin codes (target ρ > 0.8).
3. **Stability** — PPI shouldn't swing >10 points when any single proxy is dropped.

## Run

```
python etl/pipeline.py            # uses data/raw/*.csv → data/output/
```

Outputs: `ppi_pincode.csv` (joinable to boundary GeoJSON on `pincode`)
and `ppi_summary.md` (validation report).

⚠️ `data/raw/` currently holds **synthetic sample inputs** shaped exactly like
the real sources, so the pipeline is runnable today. Replace file-by-file with
real extracts; the code doesn't change.
