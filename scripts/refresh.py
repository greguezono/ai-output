"""Re-pull greguezono's PRs from GitHub, rebuild index.html, and optionally commit and push it.

Usage: python3 scripts/refresh.py [--skip-pull] [--publish]
The series ends at the last complete calendar month, so a run on the 1st covers the month just ended.
"""
import argparse
import datetime as dt
import json
import re
import statistics as st
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / ".cache" / "prs.jsonl"
AUTHOR = "greguezono"
HISTORY_START = "2018-01"
SERIES_START = "2023-11"
FIRST_AI_DATE = "2025-12-05"
BASELINE = ("2024-12", "2025-11")
PRE = ("2023-11", "2025-11")
BIGGER = ("2025-12", "2026-03")
MORE_START = "2026-04"
QUERY = """
query($q:String!,$endCursor:String){search(query:$q,type:ISSUE,first:50,after:$endCursor){
pageInfo{hasNextPage endCursor}
nodes{... on PullRequest{createdAt mergedAt state additions deletions changedFiles commits{totalCount} repository{nameWithOwner} body}}}}
"""
JQ = (
    '.data.search.nodes[]|{createdAt,mergedAt,state,additions,deletions,changedFiles,'
    'commits:.commits.totalCount,repo:.repository.nameWithOwner,'
    'claude:((.body//"")|test("Claude|claude.ai|Codex|codex";""))}'
)


def months(lo, hi):
    y, m = map(int, lo.split("-"))
    out = []
    while f"{y:04d}-{m:02d}" <= hi:
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def last_complete_month(today):
    return (today.replace(day=1) - dt.timedelta(days=1)).strftime("%Y-%m")


def month_bounds(month):
    first = dt.date.fromisoformat(month + "-01")
    nxt = (first + dt.timedelta(days=32)).replace(day=1)
    return first.isoformat(), (nxt - dt.timedelta(days=1)).isoformat()


def pull_month(month):
    lo, hi = month_bounds(month)
    cmd = ["gh", "api", "graphql", "--paginate", "-f", f"q=author:{AUTHOR} type:pr created:{lo}..{hi}",
           "-f", f"query={QUERY}", "--jq", JQ]
    error = ""
    for attempt in range(4):
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            return parse_jsonl(result.stdout, f"gh output for {month}")
        error = result.stderr.strip()[:300]
        time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"gh search failed for {month}: {error}")


def parse_jsonl(text, source):
    try:
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid JSON in {source}: {exc}") from exc


def pull(series_end):
    prs = [pr for month in months(HISTORY_START, series_end) for pr in pull_month(month)]
    RAW.parent.mkdir(exist_ok=True)
    RAW.write_text("".join(json.dumps(pr, sort_keys=True) + "\n" for pr in prs))
    print(f"pulled {len(prs)} PRs through {series_end}")


def summarize(prs, month_count):
    merged = [p for p in prs if p["mergedAt"]]
    return {
        "prs": len(prs),
        "prsPerMonth": round(len(prs) / month_count, 1),
        "mergedPerMonth": round(len(merged) / month_count, 1),
        "mergeRate": round(100 * len(merged) / len(prs)) if prs else 0,
        "medianAdditions": st.median(p["additions"] for p in prs) if prs else 0,
        "medianFiles": st.median(p["changedFiles"] for p in prs) if prs else 0,
        "churnPerMonth": round(sum(p["additions"] + p["deletions"] for p in prs) / month_count),
        "repos": len({p["repo"] for p in prs}),
        "aiMentioned": sum(p["claude"] for p in prs),
    }


def build(series_end):
    try:
        raw = RAW.read_text()
    except FileNotFoundError as exc:
        raise RuntimeError(f"{RAW} missing; run without --skip-pull first") from exc
    prs = [p for p in parse_jsonl(raw, str(RAW)) if p["createdAt"][:7] <= series_end]
    by_month = defaultdict(list)
    for p in prs:
        by_month[p["createdAt"][:7]].append(p)

    series = months(SERIES_START, series_end)
    monthly = [{"month": m, **summarize(by_month[m], 1)} for m in series]

    phase_ranges = {
        "baseline": BASELINE,
        "pre": PRE,
        "post": (FIRST_AI_DATE[:7], series_end),
        "bigger": BIGGER,
        "more": (MORE_START, series_end),
    }
    phases = {}
    for name, (lo, hi) in phase_ranges.items():
        span = months(lo, hi)
        phases[name] = {"from": lo, "to": hi, "months": len(span),
                        **summarize([p for m in span for p in by_month[m]], len(span))}

    quarterly = defaultdict(list)
    for m in series:
        y, mm = m.split("-")
        quarterly[f"{y} Q{(int(mm) - 1) // 3 + 1}"].extend(by_month[m])
    quarters = [
        {"quarter": q, "repos": len({p["repo"] for p in v}),
         "mergeRate": round(100 * sum(1 for p in v if p["mergedAt"]) / len(v))}
        for q, v in quarterly.items() if v
    ]

    multiplier = {
        k: round(phases["post"][k] / phases["baseline"][k], 1)
        for k in ("prsPerMonth", "mergedPerMonth", "medianAdditions", "churnPerMonth")
    }
    data = {
        "firstAiDate": FIRST_AI_DATE,
        "seriesEnd": series_end,
        "totalPrs": len(prs),
        "monthly": monthly,
        "quarters": quarters,
        "phases": phases,
        "multiplier": multiplier,
    }
    html = (ROOT / "template.html").read_text().replace("/*__DATA__*/null", json.dumps(data))
    (ROOT / "index.html").write_text(html)
    print(json.dumps({"seriesEnd": series_end, "totalPrs": len(prs), "multiplier": multiplier}))


def publish(series_end):
    def git(*args):
        return subprocess.run(["git", "-C", str(ROOT), *args], check=True, capture_output=True, text=True).stdout

    if not git("status", "--porcelain", "--", "index.html").strip():
        print("index.html unchanged; nothing to publish")
        return
    git("add", "index.html")
    git("commit", "-m", f"Refresh data through {series_end}")
    git("push", "origin", "HEAD")
    print("published")


def published_series_end():
    match = re.search(r'"seriesEnd": "(\d{4}-\d{2})"', (ROOT / "index.html").read_text())
    return match.group(1) if match else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--if-stale", action="store_true",
                        help="exit without network calls if index.html already covers the last complete month")
    parser.add_argument("--skip-pull", action="store_true", help="rebuild from the cached pull")
    parser.add_argument("--publish", action="store_true", help="commit and push index.html if it changed")
    args = parser.parse_args()
    series_end = last_complete_month(dt.date.today())
    if args.if_stale and published_series_end() == series_end:
        return
    print(f"{dt.datetime.now().isoformat(timespec='seconds')} refreshing through {series_end}")
    if not args.skip_pull:
        pull(series_end)
    build(series_end)
    if args.publish:
        publish(series_end)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"refresh failed: {exc}", file=sys.stderr)
        sys.exit(1)
