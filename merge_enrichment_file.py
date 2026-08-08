#!/usr/bin/env python3
"""Called by deploy.sh in place of the old wc -l row-count heuristic.

The old _restore_if_newer compared total row counts and, if the
server-side backup had more rows than the freshly-reset repo file, copied
the WHOLE backup over the repo file — discarding any content fix that had
landed in the same deploy whenever a few live-visit rows had also
accumulated locally since the last commit (found live 2026-08-08: a
15,273-row real-name fix got wholesale reverted by 3 stray rows).

Correct merge: the post-reset repo file is authoritative content for any
pincode it already has. The backup only ever contributes pincodes that
don't exist in the repo file at all — genuine server-side enrichment since
the last commit. Never lets a stale backup row override a repo row.

Usage: merge_enrichment_file.py <backup_path> <repo_path>
Exits 0 and leaves repo_path untouched on any error (missing pincode
column, unparseable CSV, etc.) — never risk corrupting the repo's already-
correct content just because the merge itself couldn't run.
"""
import sys

import pandas as pd


def main():
    if len(sys.argv) != 3:
        print("usage: merge_enrichment_file.py <backup> <repo_file>", file=sys.stderr)
        return 1
    bak_path, repo_path = sys.argv[1], sys.argv[2]

    try:
        repo = pd.read_csv(repo_path, dtype=str)
        bak = pd.read_csv(bak_path, dtype=str)
    except Exception as e:
        print(f"[merge] WARN: couldn't read {bak_path} / {repo_path} ({e}) — leaving repo version as-is")
        return 0

    if "pincode" not in repo.columns or "pincode" not in bak.columns:
        print(f"[merge] {repo_path}: no pincode column, nothing to merge")
        return 0

    bak_new = bak[bak["pincode"].notna() & (bak["pincode"] != "")]
    new_only = bak_new[~bak_new["pincode"].isin(set(repo["pincode"]))]

    if new_only.empty:
        print(f"[merge] {repo_path}: no server-side-only rows, repo version kept as-is")
        return 0

    merged = pd.concat([repo, new_only], ignore_index=True)
    merged.to_csv(repo_path, index=False)
    print(f"[merge] {repo_path}: kept repo content for {len(repo)} rows, "
          f"added {len(new_only)} server-side-only row(s) -> {len(merged)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
