#!/usr/bin/env python3
"""
Generate an audit-ready certification sign-off document from a Newman JSON
report.

Usage:
    python3 scripts/generate_signoff.py \
        --newman-json reports/newman-report.json \
        --out reports/certification-report.md \
        --partner "Reference Mock CRS (bundled)" \
        [--html-out reports/certification-report.html]

The output is a Markdown (and optionally HTML) document containing:
  - Partner name, run date, spec version
  - A per-test pass/fail table
  - Totals
  - A PASS/FAIL verdict
  - A signature block
  - For every failed assertion: the assertion message and a JSON diff of
    actual vs expected request/response payloads, where derivable.

This script only reads Newman's JSON reporter output -- it does not talk to
the network and has no dependency on the mock API being alive.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_spec_version(spec_path: Path) -> str:
    try:
        import yaml  # type: ignore

        spec = yaml.safe_load(spec_path.read_text())
        return spec.get("info", {}).get("version", "unknown")
    except Exception:
        # Fall back to a cheap regex-free scan so this script has no hard
        # dependency on PyYAML being installed.
        text = spec_path.read_text()
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("version:"):
                return line.split(":", 1)[1].strip().strip('"').strip("'")
        return "unknown"


def stringify_url(url) -> str:
    """Newman's resolved request.url is usually a structured object
    (protocol/host/path/query), not a raw string. Reconstruct something
    readable from whichever shape we get."""
    if url is None:
        return ""
    if isinstance(url, str):
        return url
    if isinstance(url, dict):
        if url.get("raw"):
            return url["raw"]
        protocol = url.get("protocol", "http")
        host = ".".join(url.get("host", [])) if isinstance(url.get("host"), list) else url.get("host", "")
        port = url.get("port")
        host_part = f"{host}:{port}" if port else host
        path = "/".join(url.get("path", [])) if isinstance(url.get("path"), list) else url.get("path", "")
        query = url.get("query") or []
        query_str = "&".join(f"{q.get('key')}={q.get('value')}" for q in query if q.get("key"))
        result = f"{protocol}://{host_part}/{path}"
        if query_str:
            result += f"?{query_str}"
        return result
    return str(url)


def collect_rows(run: dict) -> list[dict]:
    """Flatten Newman executions into one row per assertion."""
    rows = []
    for execution in run.get("executions", []):
        item_name = execution.get("item", {}).get("name", "(unnamed request)")
        request = execution.get("request", {}) or {}
        response = execution.get("response", {}) or {}
        method = request.get("method", "")
        url_raw = stringify_url(request.get("url"))
        status_code = response.get("code")

        assertions = execution.get("assertions", [])
        if not assertions:
            rows.append(
                {
                    "request": item_name,
                    "method": method,
                    "url": url_raw,
                    "status_code": status_code,
                    "assertion": "(no assertions defined)",
                    "passed": True,
                    "error": None,
                }
            )
            continue

        for assertion in assertions:
            error = assertion.get("error")
            rows.append(
                {
                    "request": item_name,
                    "method": method,
                    "url": url_raw,
                    "status_code": status_code,
                    "assertion": assertion.get("assertion", "(unnamed assertion)"),
                    "passed": error is None,
                    "error": error,
                }
            )
    return rows


def folder_for_request(collection_item_lookup: dict, request_name: str) -> str:
    return collection_item_lookup.get(request_name, "")


def build_folder_lookup(collection: dict) -> dict:
    """Map request name -> folder name, by walking the collection tree."""
    lookup = {}

    def walk(items, folder_name):
        for entry in items:
            if "item" in entry:  # it's a folder
                walk(entry["item"], entry.get("name", folder_name))
            else:
                lookup[entry.get("name")] = folder_name

    walk(collection.get("item", []), "")
    return lookup


def render_markdown(
    *,
    partner: str,
    run_date: str,
    spec_version: str,
    rows: list[dict],
    stats: dict,
    quirk_notes: dict,
) -> str:
    total_assertions = stats.get("assertions", {}).get("total", len(rows))
    failed_assertions = stats.get("assertions", {}).get("failed", sum(1 for r in rows if not r["passed"]))
    passed_assertions = total_assertions - failed_assertions
    total_requests = stats.get("requests", {}).get("total", "n/a")
    failed_requests = stats.get("requests", {}).get("failed", "n/a")

    known_quirk_failures = [r for r in rows if not r["passed"] and r["request"] in quirk_notes]
    unknown_failures = [r for r in rows if not r["passed"] and r["request"] not in quirk_notes]

    if unknown_failures:
        verdict = "FAIL"
        verdict_reason = (
            f"{len(unknown_failures)} unexpected assertion failure(s) were observed that are not "
            "attributable to a documented, deliberate mock-API quirk. Certification cannot be granted "
            "until these are triaged."
        )
    elif known_quirk_failures:
        verdict = "FAIL"
        verdict_reason = (
            f"{len(known_quirk_failures)} assertion failure(s) were observed, all attributable to "
            "documented defects in the system under test (see 'Known quirks caught' below). The "
            "certification harness is working as designed: it caught real, reproducible API defects. "
            "The system under test does NOT pass certification until these are fixed."
        )
    else:
        verdict = "PASS"
        verdict_reason = "All assertions passed. No defects were detected during this certification run."

    lines = []
    lines.append("# CRS Certification Report")
    lines.append("")
    lines.append(f"- **Partner / System under test:** {partner}")
    lines.append(f"- **Certification date:** {run_date}")
    lines.append(f"- **Contract (OpenAPI spec) version:** {spec_version}")
    lines.append("- **Test collection:** `collection/crs-certification.postman_collection.json`")
    lines.append("- **Executed via:** Newman (Postman CLI runner)")
    lines.append("- **Generated by:** `scripts/generate_signoff.py` (this file is generated, not hand-written)")
    lines.append("")
    lines.append(f"## Verdict: {verdict}")
    lines.append("")
    lines.append(verdict_reason)
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(f"| Requests executed | {total_requests} |")
    lines.append(f"| Requests with a failure | {failed_requests} |")
    lines.append(f"| Assertions executed | {total_assertions} |")
    lines.append(f"| Assertions passed | {passed_assertions} |")
    lines.append(f"| Assertions failed | {failed_assertions} |")
    pct = (passed_assertions / total_assertions * 100) if total_assertions else 0.0
    lines.append(f"| Pass rate | {pct:.1f}% |")
    lines.append("")

    lines.append("## Per-test results")
    lines.append("")
    lines.append("| # | Folder | Request | Assertion | Result |")
    lines.append("|---|---|---|---|---|")
    for i, row in enumerate(rows, start=1):
        result = "PASS" if row["passed"] else "**FAIL**"
        folder = row.get("folder", "")
        assertion = row["assertion"].replace("|", "\\|")
        request_name = row["request"].replace("|", "\\|")
        lines.append(f"| {i} | {folder} | {request_name} | {assertion} | {result} |")
    lines.append("")

    if any(not r["passed"] for r in rows):
        lines.append("## Failure detail")
        lines.append("")
        for row in rows:
            if row["passed"]:
                continue
            lines.append(f"### {row['request']} — {row['assertion']}")
            lines.append("")
            lines.append(f"- **Request:** `{row['method']} {row['url']}`")
            lines.append(f"- **Response status code:** `{row['status_code']}`")
            note = quirk_notes.get(row["request"])
            if note:
                lines.append(f"- **Known cause:** {note}")
            error = row.get("error") or {}
            message = error.get("message", "(no message captured)")
            lines.append("")
            lines.append("**Assertion message (actual vs. expected):**")
            lines.append("")
            lines.append("```text")
            lines.append(message)
            lines.append("```")
            lines.append("")

    lines.append("## Known quirks caught by this run")
    lines.append("")
    if known_quirk_failures:
        for row in known_quirk_failures:
            lines.append(f"- **{row['request']}**: {quirk_notes[row['request']]}")
    else:
        lines.append("- None of the documented quirk tests failed in this run.")
    lines.append("")

    lines.append("## Sign-off")
    lines.append("")
    lines.append("This report was generated automatically from a live test execution against the ")
    lines.append("system under test. No results in this document were hand-edited.")
    lines.append("")
    lines.append("| Role | Name | Signature | Date |")
    lines.append("|---|---|---|---|")
    lines.append("| Certification engineer | Pakshal Shah | _(automated run — no manual signature)_ | " + run_date + " |")
    lines.append("| Partner representative | _____________________ | _____________________ | __________ |")
    lines.append("")
    lines.append("---")
    lines.append("*Generated by the CRS Certification Harness — https://pakshalshah.com*")
    lines.append("")

    return "\n".join(lines)


def render_html(markdown_body: str, title: str) -> str:
    # Minimal, dependency-free Markdown->HTML good enough for a report page.
    try:
        import markdown  # type: ignore

        body_html = markdown.markdown(markdown_body, extensions=["tables"])
    except Exception:
        # Extremely small fallback renderer: paragraphs + preformatted body.
        import html as _html

        body_html = "<pre>" + _html.escape(markdown_body) + "</pre>"

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
body {{ font-family: -apple-system, Segoe UI, Helvetica, Arial, sans-serif; max-width: 900px; margin: 2rem auto; padding: 0 1rem; color: #1a1a1a; }}
table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; }}
th, td {{ border: 1px solid #ddd; padding: 6px 10px; text-align: left; font-size: 0.92rem; }}
th {{ background: #f4f4f4; }}
code, pre {{ background: #f6f8fa; }}
pre {{ padding: 0.75rem; overflow-x: auto; }}
h1 {{ border-bottom: 3px solid #222; padding-bottom: .3rem; }}
h2 {{ border-bottom: 1px solid #ccc; padding-bottom: .2rem; margin-top: 2rem; }}
</style>
</head>
<body>
{body_html}
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--newman-json", default=str(ROOT / "reports" / "newman-report.json"))
    parser.add_argument("--out", default=str(ROOT / "reports" / "certification-report.md"))
    parser.add_argument("--html-out", default=str(ROOT / "reports" / "certification-report.html"))
    parser.add_argument("--partner", default="Reference Mock CRS (bundled with this repo)")
    parser.add_argument("--spec", default=str(ROOT / "spec" / "crs-openapi.yaml"))
    args = parser.parse_args()

    newman_path = Path(args.newman_json)
    if not newman_path.exists():
        print(f"error: newman JSON report not found at {newman_path}", file=sys.stderr)
        sys.exit(2)

    data = json.loads(newman_path.read_text())
    run = data.get("run", {})
    collection = data.get("collection", {})

    folder_lookup = build_folder_lookup(collection)
    rows = collect_rows(run)
    for row in rows:
        row["folder"] = folder_lookup.get(row["request"], "")

    quirk_notes = {
        "POST /reservations - QUIRK: checkout before checkin should be rejected": (
            "CRS-QUIRK-1 — the API accepts a reservation where `checkout` is before `checkin` "
            "instead of rejecting it with a 4xx response."
        ),
        "POST /reservations - QUIRK: guest_name over 50 chars must round-trip exactly": (
            "CRS-QUIRK-3 — `guest_name` values longer than 50 characters are silently truncated "
            "instead of being rejected or preserved."
        ),
        "PUT /reservations/{id} - QUIRK: unknown id should return 404": (
            "CRS-QUIRK-2 — updating a reservation id that does not exist returns HTTP 200 with an "
            "`{\"error\": ...}` body instead of HTTP 404."
        ),
    }

    run_date = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    spec_version = load_spec_version(Path(args.spec))

    markdown_report = render_markdown(
        partner=args.partner,
        run_date=run_date,
        spec_version=spec_version,
        rows=rows,
        stats=run.get("stats", {}),
        quirk_notes=quirk_notes,
    )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(markdown_report)
    print(f"Wrote {out_path}")

    if args.html_out:
        html_path = Path(args.html_out)
        html_path.write_text(render_html(markdown_report, title="CRS Certification Report"))
        print(f"Wrote {html_path}")


if __name__ == "__main__":
    main()
