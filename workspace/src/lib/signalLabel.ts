// Turn a raw signal column key into something readable:
//   car_2w_ratio → "Car 2W ratio", ev_share → "EV share".
const SIGNAL_WORD: Record<string, string> = {
  "2w": "2W", ev: "EV", upi: "UPI", ppi: "PPI", msme: "MSME", nsdp: "NSDP",
  rto: "RTO", itr: "ITR", sfb: "SFB", rrb: "RRB", poi: "POI", psu: "PSU",
  hces: "HCES", mpce: "MPCE", lmv: "LMV",
};

export function prettySignal(key: string): string {
  return key
    .split("_")
    .map((w, i) => SIGNAL_WORD[w] ?? (i === 0 ? w.charAt(0).toUpperCase() + w.slice(1) : w))
    .join(" ");
}
