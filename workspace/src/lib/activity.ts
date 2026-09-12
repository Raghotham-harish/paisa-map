import { ActivityEntry } from "./api";

// Single source of truth — Dashboard's mini feed and the full Activity page
// used to keep their own copies of this and had drifted: Activity.tsx's was
// missing location_score/location_compare/forecast_run/report_generate, so
// those entries fell back to the raw backend action string on the one page
// that's supposed to show the complete, humanized history.
export const ACTION_LABELS: Record<string, string> = {
  login: "Signed in",
  project_create: "Created project",
  location_save: "Saved a location",
  location_score: "Scored a location",
  location_compare: "Compared locations",
  forecast_run: "Ran a forecast",
  report_generate: "Generated a report",
  report_download: "Downloaded a report",
  report_share_view: "Report viewed via share link",
  data_export: "Exported data",
};

export function describeActivity(entry: ActivityEntry): string {
  const label = ACTION_LABELS[entry.action] || entry.action;
  const pincode = (entry.metadata as any)?.pincode;
  return pincode ? `${label} — ${pincode}` : label;
}
