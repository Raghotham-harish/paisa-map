// A stored plan id as a person should read it. Companies hold either a legacy
// id ('free' / 'pro' / 'team') or a price-book tier stored as 'v2_<tier>'.
const V2_LABELS: Record<string, string> = {
  trial: "Trial", starter: "Starter", growth: "Growth", scale: "Scale", pro: "Pro", enterprise: "Enterprise",
};

export function planLabel(plan?: string | null): string {
  if (!plan || plan === "free") return "Free";
  if (plan.startsWith("v2_")) return V2_LABELS[plan.slice(3)] ?? plan;
  if (plan === "pro" || plan === "team") return `${plan[0].toUpperCase()}${plan.slice(1)} (legacy)`;
  return plan;
}
