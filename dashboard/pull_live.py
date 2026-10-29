"""Copy the published Job Radar data back down from the live site.

GitHub Pages replaces the whole site on every deploy, so a workflow that only
changes one file (the fresh-jobs run adds data/fresh.json, the daily build
rebuilds everything else) must first fetch the files it isn't rebuilding.

    python dashboard/pull_live.py https://you.github.io/ats-scrapers/data dashboard/site/data --feed
    python dashboard/pull_live.py https://you.github.io/ats-scrapers/data dashboard/site/data --fresh
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx


def get(client: httpx.Client, url: str) -> bytes | None:
    resp = client.get(url)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.content


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("base", help="Live data/ URL")
    parser.add_argument("dest", help="Local data/ folder")
    parser.add_argument("--feed", action="store_true", help="index.json, seen.json and role files")
    parser.add_argument(
        "--fresh", action="store_true", help="fresh.json and fresh_companies.json, if published"
    )
    parser.add_argument("--app-version", help="Rewrite index.json's app_version to this")
    args = parser.parse_args()

    base, dest = args.base.rstrip("/"), Path(args.dest)
    (dest / "roles").mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=120, follow_redirects=True) as client:
        if args.feed:
            raw = get(client, f"{base}/index.json")
            if raw is None:
                print("::error::No published feed yet; run the main Job Radar workflow first")
                return 1
            index = json.loads(raw)
            if args.app_version:
                index["app_version"] = args.app_version
            (dest / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1))
            for name in ["seen.json"] + [info["file"] for info in index["roles"].values()]:
                body = get(client, f"{base}/{name}")
                if body is None:
                    print(f"::error::{name} is missing from the live site")
                    return 1
                (dest / name).write_bytes(body)
            print(f"pulled feed: {len(index['roles'])} role files")
        if args.fresh:
            # fresh.json for /fresh.html, fresh_companies.json = cached company list
            for name in ("fresh.json", "fresh_companies.json"):
                body = get(client, f"{base}/{name}")
                if body is not None:
                    (dest / name).write_bytes(body)
                    print(f"pulled {name}")
                else:
                    print(f"no {name} published yet")
    return 0


if __name__ == "__main__":
    sys.exit(main())
