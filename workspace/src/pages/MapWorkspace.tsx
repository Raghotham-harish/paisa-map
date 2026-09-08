import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, Project, SignalCatalogItem } from "../lib/api";
import { MapStyle, MAP_SRC, MyStorePoint, SUITABILITY_KEY, useMapBridge } from "../lib/mapBridge";
import { useAuth } from "../lib/auth";
import { saveMapSession } from "../lib/mapSession";
import { FilterBar } from "../components/map/FilterBar";
import { MapControls } from "../components/map/MapControls";
import { LocationPanel } from "../components/map/LocationPanel";
import { KpiStrip } from "../components/map/KpiStrip";
import { CompareModal } from "../components/map/CompareModal";

export default function MapWorkspace() {
  const { user } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const bridge = useMapBridge(iframeRef);

  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState<number | null>(null);
  const [catalog, setCatalog] = useState<SignalCatalogItem[]>([]);
  const [signals, setSignals] = useState<string[]>([]);
  const [primarySignal, setPrimarySignal] = useState<string>("ppi_ml");
  const [compareOpen, setCompareOpen] = useState(false);
  const [signInPrompt, setSignInPrompt] = useState(false);
  const [mapStyle, setMapStyle] = useState<MapStyle>("symbol");
  const [ringsOn, setRingsOn] = useState(false);
  const [myStoresOn, setMyStoresOn] = useState(false);
  // Fetched whenever the project changes, independent of the toggle — the
  // toggle's checkbox needs a real count (to grey itself out when the
  // project has no geocoded stores) even before it's ever been switched on.
  const [myStoreCandidates, setMyStoreCandidates] = useState<MyStorePoint[]>([]);
  const myStores = myStoresOn ? myStoreCandidates : [];

  const project = useMemo(
    () => projects.find((p) => p.id === projectId) ?? null,
    [projects, projectId],
  );

  useEffect(() => {
    api.signalCatalog().then((d) => setCatalog(d.signals)).catch(() => setCatalog([]));
    api.listProjects().then((d) => {
      setProjects(d.projects);
      const fromUrl = Number(searchParams.get("project_id"));
      const initial = d.projects.find((p) => p.id === fromUrl) ?? d.projects[0];
      if (initial) setProjectId(initial.id);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // When the selected project changes, seed the signal filter from its saved
  // signals (falling back to PPI) and reflect it in the URL.
  useEffect(() => {
    if (projectId == null) return;
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set("project_id", String(projectId));
      return next;
    }, { replace: true });
    const p = projects.find((x) => x.id === projectId);
    const known = new Set(catalog.map((c) => c.key));
    const seeded = (p?.signals ?? []).filter((s) => known.has(s));
    setSignals(seeded.length ? seeded : ["ppi_ml"]);
    // Default to the project-fit layer whenever the project carries enough
    // context to compute it (chosen signals and/or an average ticket) — that's
    // the whole point of picking a project on the map. Otherwise fall back to
    // the first raw signal.
    const canScoreFit = seeded.length > 0 || p?.avg_ticket != null;
    setPrimarySignal(canScoreFit ? SUITABILITY_KEY : seeded[0] ?? "ppi_ml");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, projects, catalog]);

  // Push the primary signal to the map once it's ready / whenever it changes.
  useEffect(() => {
    if (bridge.ready) bridge.send({ type: "setSignal", col: primarySignal });
  }, [bridge.ready, primarySignal, bridge]);

  // Suitability layer — instant client-side preview, recomputed on every
  // project / signal change (no round-trip), then reconciled to the
  // authoritative /api/expansion/surface scores when they land. `bridge.send`
  // is a stable useCallback ref, so it (not the whole `bridge` object, which is
  // a fresh literal every render) is what these effects depend on — otherwise
  // the async fetch below gets cancelled and re-fired on every viewport tick.
  const sendToMap = bridge.send;
  useEffect(() => {
    if (!bridge.ready || primarySignal !== SUITABILITY_KEY || !project) return;
    sendToMap({
      type: "setSuitability",
      mode: "preview",
      label: project.name,
      signals: signals.filter((s) => s !== SUITABILITY_KEY),
      avgTicket: project.avg_ticket ?? null,
    });
  }, [bridge.ready, primarySignal, project, signals, sendToMap]);

  useEffect(() => {
    if (primarySignal !== SUITABILITY_KEY || projectId == null || !bridge.ready) return;
    let cancelled = false;
    api.getExpansionSurface(projectId)
      .then((d) => {
        if (cancelled) return;
        const scores: Record<string, number> = {};
        for (const s of d.scores) scores[s.pincode] = s.combined_score;
        sendToMap({ type: "setSuitability", mode: "scored", scores });
      })
      .catch(() => {/* preview stands; a failed scoring pass shouldn't blank the layer */});
    return () => { cancelled = true; };
  }, [primarySignal, projectId, bridge.ready, sendToMap]);

  // Deep-link from the dashboard's "Resume on the map" — fly to the
  // last-selected pincode once, then drop the param so it doesn't re-fire on
  // every unrelated re-render (or override a later manual selection).
  useEffect(() => {
    const pincode = searchParams.get("pincode");
    if (!pincode || !bridge.ready) return;
    bridge.send({ type: "flyTo", pincode });
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.delete("pincode");
      return next;
    }, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bridge.ready]);

  useEffect(() => {
    if (bridge.ready) bridge.send({ type: "setRepresentation", repr: mapStyle });
  }, [bridge.ready, mapStyle, bridge]);

  const catchmentKm = project?.catchment_km ?? 3;
  useEffect(() => {
    if (bridge.ready) bridge.send({ type: "setRings", on: ringsOn, radiusKm: catchmentKm });
  }, [bridge.ready, ringsOn, catchmentKm, bridge]);

  // Fetched per project regardless of the toggle (see myStoreCandidates'
  // comment above) — cheap, a project's own store list is never large.
  useEffect(() => {
    if (projectId == null) {
      setMyStoreCandidates([]);
      return;
    }
    let cancelled = false;
    api.listCustomerLocations(projectId).then((d) => {
      if (cancelled) return;
      const pts: MyStorePoint[] = d.locations
        .filter((l) => l.lat != null && l.lng != null)
        .map((l) => ({ lat: l.lat as number, lng: l.lng as number, name: l.store_name }));
      setMyStoreCandidates(pts);
    }).catch(() => {
      if (!cancelled) setMyStoreCandidates([]);
    });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  useEffect(() => {
    if (bridge.ready) bridge.send({ type: "setMyStores", stores: myStores });
  }, [bridge.ready, myStores, bridge]);

  // "Pick up where you left off" — see lib/mapSession.ts. Written on every
  // selection/compare-basket change, read back by the Dashboard hero.
  useEffect(() => {
    saveMapSession({
      projectId,
      pincode: bridge.selected?.pincode ?? null,
      name: bridge.selected?.name ?? null,
      compareCount: bridge.compare.length,
    });
  }, [projectId, bridge.selected, bridge.compare]);

  // Map asked for sign-in (Save while signed out).
  useEffect(() => {
    if (bridge.needAuthAt && !user) setSignInPrompt(true);
  }, [bridge.needAuthAt, user]);

  // After the user signs in on this page, tell the map to re-check auth.
  useEffect(() => {
    if (user && bridge.ready) bridge.send({ type: "refreshAuth" });
  }, [user, bridge.ready, bridge]);

  return (
    <div className="map-shell">
      <FilterBar
        projects={projects}
        project={project}
        onProjectChange={setProjectId}
        catalog={catalog}
        signals={signals}
        onSignalsChange={(next) => {
          setSignals(next);
          // In Suitability mode the signal list feeds the fit score — stay on it.
          if (primarySignal === SUITABILITY_KEY) return;
          if (!next.length) { setPrimarySignal("ppi_ml"); return; }
          const added = next.find((s) => !signals.includes(s));
          if (added) setPrimarySignal(added);
          else if (!next.includes(primarySignal)) setPrimarySignal(next[0]);
        }}
        primarySignal={primarySignal}
        onPrimaryChange={setPrimarySignal}
        suitabilityAvailable={project != null}
      />

      <div className="map-body">
        <iframe
          ref={iframeRef}
          className="map-frame"
          src={MAP_SRC}
          title="PaisaMap map"
          allow="geolocation"
        />

        <MapControls
          catalog={catalog}
          primarySignal={primarySignal}
          onPrimaryChange={(v) => {
            setPrimarySignal(v);
            if (v !== SUITABILITY_KEY && !signals.includes(v)) setSignals([v, ...signals]);
          }}
          suitabilityAvailable={project != null}
          mapStyle={mapStyle}
          onMapStyleChange={setMapStyle}
          ringsOn={ringsOn}
          onRingsToggle={setRingsOn}
          catchmentKm={catchmentKm}
          myStoresOn={myStoresOn}
          onMyStoresToggle={setMyStoresOn}
          myStoresCount={myStoreCandidates.length}
          kpis={bridge.kpis}
        />

        <LocationPanel
          selection={bridge.selected}
          project={project}
          inCompare={bridge.selected?.pincode ? bridge.compare.includes(bridge.selected.pincode) : false}
          onToggleCompare={(pincode) => bridge.send({ type: "toggleCompare", pincode })}
          onSaved={() => {/* toast handled inside the map */}}
        />

        {bridge.compare.length > 0 && (
          <div className="compare-basket">
            <span className="kicker">Compare basket</span>
            <div className="compare-basket-chips">
              {bridge.compare.map((pc) => (
                <span className="ms-chip" key={pc}>
                  {pc}
                  <button type="button" aria-label={`Remove ${pc}`} onClick={() => bridge.send({ type: "toggleCompare", pincode: pc })}>
                    <svg width="10" height="10" viewBox="0 0 10 10" stroke="currentColor" strokeWidth="1.6"><path d="M2 2l6 6M8 2l-6 6" /></svg>
                  </button>
                </span>
              ))}
            </div>
            <button className="btn" disabled={bridge.compare.length < 2} onClick={() => setCompareOpen(true)}>
              Compare ({bridge.compare.length})
            </button>
          </div>
        )}

        <KpiStrip kpis={bridge.kpis} />
      </div>

      {compareOpen && (
        <CompareModal
          pincodes={bridge.compare}
          project={project}
          onClose={() => setCompareOpen(false)}
        />
      )}

      {signInPrompt && !user && (
        <div className="map-signin-overlay" onClick={(e) => e.target === e.currentTarget && setSignInPrompt(false)}>
          <div className="card map-signin-card">
            <h3 style={{ margin: "0 0 6px" }}>Sign in to save locations</h3>
            <p style={{ fontSize: 13, color: "var(--ink-soft)", margin: "0 0 14px" }}>
              Saving a location, building a shortlist and generating reports all need an account.
            </p>
            <a className="btn" href="/workspace/">Sign in</a>
            <button className="btn secondary" style={{ marginLeft: 8 }} onClick={() => setSignInPrompt(false)}>Not now</button>
          </div>
        </div>
      )}
    </div>
  );
}
