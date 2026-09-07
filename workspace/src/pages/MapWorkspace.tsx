import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, Project, SignalCatalogItem } from "../lib/api";
import { MAP_SRC, useMapBridge } from "../lib/mapBridge";
import { useAuth } from "../lib/auth";
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
    setPrimarySignal(seeded[0] ?? "ppi_ml");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, projects, catalog]);

  // Push the primary signal to the map once it's ready / whenever it changes.
  useEffect(() => {
    if (bridge.ready) bridge.send({ type: "setSignal", col: primarySignal });
  }, [bridge.ready, primarySignal, bridge]);

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
          if (next.length && !next.includes(primarySignal)) setPrimarySignal(next[0]);
          if (!next.length) setPrimarySignal("ppi_ml");
        }}
        primarySignal={primarySignal}
        onPrimaryChange={setPrimarySignal}
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
            if (!signals.includes(v)) setSignals([v, ...signals]);
          }}
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
