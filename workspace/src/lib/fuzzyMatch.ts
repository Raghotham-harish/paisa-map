// Lightweight, dependency-free "did you mean…" matcher for free-text custom
// values against a fixed option catalog (e.g. the signal catalog). Token
// overlap rather than edit-distance: catalog labels are short phrases
// ("Cold storage capacity (govt)"), so "does the typed phrase share most of
// its words with this label" is a better fit than character-level fuzziness.

export interface FuzzyOption {
  value: string;
  label: string;
}

const TOKEN_SPLIT = /[\s,/()&-]+/;

function tokenize(s: string): string[] {
  return s
    .toLowerCase()
    .split(TOKEN_SPLIT)
    .map((t) => t.trim())
    .filter(Boolean);
}

/** Returns the catalog option whose label best overlaps the query's words,
 *  or undefined if nothing clears `threshold` (fraction of query tokens
 *  found in the label, exact-or-substring per token). */
export function fuzzyBestMatch<T extends FuzzyOption>(
  query: string,
  options: T[],
  threshold = 0.5,
): T | undefined {
  const qTokens = tokenize(query);
  if (qTokens.length === 0) return undefined;

  let best: { opt: T; score: number } | undefined;
  for (const opt of options) {
    const labelTokens = tokenize(opt.label);
    if (labelTokens.length === 0) continue;
    let hits = 0;
    for (const qt of qTokens) {
      if (labelTokens.some((lt) => lt === qt || lt.includes(qt) || qt.includes(lt))) hits++;
    }
    const score = hits / qTokens.length;
    if (!best || score > best.score) best = { opt, score };
  }
  return best && best.score >= threshold ? best.opt : undefined;
}
