"""Run a pytest suite folder with a chosen parser and report pass/fail, LLM latency and rules-fallback rate.

    python scripts/eval_parser.py --parser local --suite tests/benign --out out/eval_local_benign
Sets BG_PARSER (and BG_LOCAL_MODEL with --model); LocalLLMParser appends one record per call to BG_LLM_STATS.
Writes report.json and report.md to the output folder."""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parser", default="local", choices=["rules", "local", "claude"])
    ap.add_argument("--suite", required=True)
    ap.add_argument("--model", default=None)
    ap.add_argument("--out", default=None, help="output folder (default out/eval_<parser>_<suite>)")
    ap.add_argument("--runxfail", action="store_true",
                    help="ignore xfail markers (they document rules-parser limits); count every case as pass/fail")
    a = ap.parse_args()
    out = Path(a.out or ROOT / "out" / f"eval_{a.parser}_{Path(a.suite).name}")
    out.mkdir(parents=True, exist_ok=True)
    stats, xml = out / "llm_calls.jsonl", out / "junit.xml"
    stats.unlink(missing_ok=True)
    env = {**os.environ, "BG_PARSER": a.parser, "BG_LLM_STATS": str(stats)}
    if a.model:
        env["BG_LOCAL_MODEL"] = a.model
    subprocess.run([sys.executable, "-m", "pytest", a.suite, "-q", "-p", "no:cacheprovider",
                    f"--junitxml={xml}", *(["--runxfail"] if a.runxfail else [])], cwd=ROOT, env=env)
    cases = []
    for tc in ET.parse(xml).getroot().iter("testcase"):
        st = "failed" if tc.find("failure") is not None or tc.find("error") is not None else (
            "skipped" if tc.find("skipped") is not None else "passed")
        cases.append({"case": f"{tc.get('classname')}::{tc.get('name')}", "status": st})
    calls = [json.loads(l) for l in stats.read_text(encoding="utf-8").splitlines() if l.strip()] if stats.exists() else []
    lat = sorted(c["latency_s"] for c in calls if not c["fell_back"])
    fb = [c for c in calls if c["fell_back"]]
    reasons: dict[str, int] = {}
    for c in fb:
        reasons[c["reason"]] = reasons.get(c["reason"], 0) + 1
    n = {k: sum(c["status"] == k for c in cases) for k in ("passed", "failed", "skipped")}
    rep = {"parser": a.parser, "model": a.model or env.get("BG_LOCAL_MODEL", "default"), "suite": a.suite,
           "cases": len(cases), **n, "llm_calls": len(calls), "fallback_calls": len(fb),
           "fallback_rate": round(len(fb) / len(calls), 4) if calls else None, "fallback_reasons": reasons,
           "latency_s": {"mean": round(statistics.mean(lat), 3), "median": round(statistics.median(lat), 3),
                         "p95": round(lat[int(0.95 * (len(lat) - 1))], 3), "max": lat[-1]} if lat else None,
           "results": cases}
    (out / "report.json").write_text(json.dumps(rep, indent=2), encoding="utf-8")
    md = [f"# Parser evaluation: {a.parser} ({rep['model']}) on {a.suite}", "",
          f"- cases: {len(cases)}  passed: {n['passed']}  failed: {n['failed']}  skipped: {n['skipped']}",
          f"- LLM calls: {len(calls)}  fell back to rules: {len(fb)} (rate {rep['fallback_rate']})  reasons: {reasons}",
          f"- latency per successful call (s): {rep['latency_s']}", "", "| case | status |", "|---|---|"]
    md += [f"| {c['case']} | {c['status']} |" for c in cases]
    (out / "report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in rep.items() if k != "results"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
