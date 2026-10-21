import collections, json, re, sys, httpx, pyarrow.parquet as pq
def note(title, lines):
    msg = "%0A".join(l.replace("%", "%25").replace("\n", " ")[:300] for l in lines)[:3900]
    print(f"::notice title={title}::{msg}")
feed = httpx.get(sys.argv[1], timeout=60).json()
jobs = feed["jobs"]
note("places", [f"{k}: {v}" for k, v in collections.Counter(j["place"] for j in jobs).items()] + [f"total {len(jobs)}"])
for place in ("remote", "india", "home"):
    c = collections.Counter(j["location"] for j in jobs if j["place"] == place)
    note(f"loc-{place}", [f"{n} | {l}" for l, n in c.most_common(45)])
odd = [j["location"] for j in jobs if re.search(r"australia|chile|sydney|melbourne|santiago|brazil|mexico|colombia|argentin|peru|south africa", j["location"], re.I)]
c = collections.Counter(odd); note("odd-locs", [f"{n} | {l}" for l, n in c.most_common(45)] + [f"odd total {len(odd)}"])
oddplace = collections.Counter(j["place"] for j in jobs if j["location"] in c); note("odd-places", [f"{k}: {v}" for k, v in oddplace.items()])
# visa / relocation phrasing in greenhouse
man = httpx.get("https://storage.stapply.ai/jobhive/v1/manifest.json", timeout=60).json()
url = man["by_ats"]["greenhouse"]["parquet"]
with httpx.stream("GET", url, timeout=600, follow_redirects=True) as r, open("gh.parquet", "wb") as f:
    for ch in r.iter_bytes(1 << 20): f.write(ch)
pf = pq.ParquetFile("gh.parquet")
rx = re.compile(r"(visa|sponsor|relocat)", re.I)
ctx = collections.Counter(); n = 0
for b in pf.iter_batches(batch_size=20000, columns=["title", "description", "location", "country_iso", "is_remote"]):
    for row in b.to_pylist():
        if not re.search(r"engineer|developer", row["title"] or "", re.I): continue
        d = re.sub(r"<[^>]+>|&nbsp;", " ", row["description"] or "")
        d = re.sub(r"\s+", " ", d)
        for m in rx.finditer(d):
            s = d[max(0, m.start() - 70): m.end() + 50].lower()
            s = re.sub(r"[^a-z ]", "", s)
            ctx[s] += 1
        n += 1
note("visa-ctx-1", [f"{k} | {v}" for v, k in []] + [f"{c} | {s}" for s, c in ctx.most_common(14)])
note("visa-ctx-2", [f"{c} | {s}" for s, c in ctx.most_common(28)[14:]])
import random; random.seed(1); rare = [s for s, c in ctx.items() if c == 1]; random.shuffle(rare)
note("visa-ctx-rare", rare[:14]); note("visa-ctx-rare2", rare[14:28])
locs = collections.Counter()
for b in pf.iter_batches(batch_size=50000, columns=["location", "country_iso", "is_remote"]):
    for row in b.to_pylist():
        l = row["location"] or ""
        if re.search(r"remote", l, re.I): locs[(l, row["country_iso"], row["is_remote"])] += 1
note("gh-remote-locs", [f"{c} | {l} | {ci} | {r}" for (l, ci, r), c in locs.most_common(45)])
note("gh-cols", [str(pf.schema_arrow.names), f"eng rows {n}"])
