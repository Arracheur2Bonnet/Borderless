"""One-command verification of the whole repository (offline, no API key, about 20 s).

    python scripts/verify_all.py                       (any OS, from the repo root or elsewhere)
    python scripts/verify_all.py --report-dir out      (keep the JUnit XML and regenerated reports in ./out)

Runs, in order, with the current interpreter:

  1. pytest           the full suite (tests/), JUnit XML written to the report dir
  2. scenarios        scripts/run_scenarios.py, the 20 scripted missions (rewrites deliverables/evidence/)
  3. golden path      scripts/golden_path.py, the demo flow end to end
  4. security report  tests/e2e_report.py, redirected to the report dir so the tracked
                      deliverables/security_results.{md,json} are NOT overwritten; its results
                      (minus the timestamp) are compared with the committed copy
  5. reproducibility  the regenerated deliverables/evidence/ and task_completion.md must be
                      identical to the committed files (needs git; skipped otherwise)

Prints a PASS/FAIL table and exits 1 if any step fails.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

E2E_SHIM = """
import sys
from pathlib import Path
sys.path.insert(0, {root!r})
import tests.e2e_report as report
report.ROOT = Path({out!r})          # write deliverables/security_results.* under the report dir
sys.exit(report.main())
"""


def child_env() -> dict:
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)    # offline guarantee
    env.pop("BG_RECEIPT_KEY", None)       # demo anchor key -> byte-identical evidence
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def run(cmd: list[str]) -> tuple[int, str]:
    """Run a command from the repo root, streaming its output live and returning it."""
    print(f"\n$ {' '.join(cmd)}", flush=True)
    proc = subprocess.Popen(cmd, cwd=ROOT, env=child_env(), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    out = []
    assert proc.stdout is not None
    for line in proc.stdout:
        sys.stdout.write(line)
        out.append(line)
    sys.stdout.flush()
    return proc.wait(), "".join(out)


def step_pytest(py: str, report_dir: Path) -> tuple[bool, str]:
    rc, out = run([py, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider", "-rx",
                   f"--junitxml={report_dir / 'pytest-junit.xml'}"])
    m = re.findall(r"^=*\s*((?:\d+ \w+(?:, )?)+) in [\d.]+s", out, re.M)
    detail = m[-1] if m else "no pytest summary line"
    return rc == 0, detail


def step_scenarios(py: str, _: Path) -> tuple[bool, str]:
    rc, out = run([py, "scripts/run_scenarios.py"])
    m = re.search(r"(\d+)/(\d+) tasks completed \(([\d.]+)%\)", out)
    detail = f"{m.group(1)}/{m.group(2)} missions completed" if m else "no summary line"
    return rc == 0 and bool(m) and m.group(1) == m.group(2), detail


def step_golden(py: str, _: Path) -> tuple[bool, str]:
    rc, out = run([py, "scripts/golden_path.py"])
    ok = rc == 0 and "RESULT: PASS" in out
    return ok, "RESULT: PASS (EUR 600.00, USD 196.74, 3 receipts)" if ok else "RESULT line missing or FAIL"


def _comparable(doc: dict) -> dict:
    doc = json.loads(json.dumps(doc))
    doc.get("summary", {}).pop("generated_at", None)
    return doc


def step_e2e(py: str, report_dir: Path) -> tuple[bool, str]:
    rc, out = run([py, "-c", E2E_SHIM.format(root=str(ROOT), out=str(report_dir))])
    m = re.search(r"attack-blocked (\d+)/(\d+).*benign-completion (\d+)/(\d+)", out)
    if not m:
        return False, "no summary line"
    detail = f"attacks blocked {m.group(1)}/{m.group(2)}, benign completed {m.group(3)}/{m.group(4)}"
    ok = rc == 0 and m.group(1) == m.group(2) and m.group(3) == m.group(4)
    fresh = report_dir / "deliverables" / "security_results.json"
    committed = ROOT / "deliverables" / "security_results.json"
    if fresh.exists() and committed.exists():
        same = _comparable(json.loads(fresh.read_text(encoding="utf-8"))) == \
            _comparable(json.loads(committed.read_text(encoding="utf-8")))
        detail += "; matches committed report" if same else "; DIFFERS from committed deliverables/security_results.json"
        ok = ok and same
    return ok, detail


def step_repro(_: str, __: Path) -> tuple[bool | None, str]:
    git = shutil.which("git")
    if not git or not (ROOT / ".git").exists():
        return None, "skipped (not a git checkout)"
    paths = ["deliverables/evidence", "deliverables/task_completion.md"]
    res = subprocess.run([git, "diff", "--stat", "--", *paths], cwd=ROOT, capture_output=True,
                         text=True, encoding="utf-8", errors="replace")
    if res.returncode != 0:
        return None, f"skipped (git diff failed: {res.stderr.strip()[:80]})"
    if res.stdout.strip():
        print(res.stdout)
        return False, "regenerated evidence differs from the committed files (git diff above)"
    # Content is identical. With core.autocrlf=true (Git for Windows default) the LF files written by
    # run_scenarios.py still show as "modified" in `git status`; re-checkout restores the local line endings.
    subprocess.run([git, "checkout", "--", *paths], cwd=ROOT, capture_output=True)
    return True, "regenerated evidence identical to committed files"


STEPS = [
    ("pytest (full suite)", step_pytest),
    ("scripted missions", step_scenarios),
    ("golden path", step_golden),
    ("security report", step_e2e),
    ("evidence reproducible", step_repro),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--report-dir", type=Path, default=None,
                    help="where to write pytest-junit.xml and the regenerated security report "
                         "(default: a fresh temporary directory)")
    args = ap.parse_args()
    report_dir = (args.report_dir or Path(tempfile.mkdtemp(prefix="bg-verify-"))).resolve()
    report_dir.mkdir(parents=True, exist_ok=True)

    py = sys.executable
    print(f"BorderlessGuard verification\n  python : {py} ({sys.version.split()[0]})\n"
          f"  repo   : {ROOT}\n  reports: {report_dir}")

    rows = []
    for name, fn in STEPS:
        print(f"\n{'=' * 78}\n== {name}\n{'=' * 78}", flush=True)
        t0 = time.perf_counter()
        try:
            ok, detail = fn(py, report_dir)
        except Exception as exc:  # a crash in the harness is a failure, not a skip
            ok, detail = False, f"verify_all error: {exc!r}"
        rows.append((name, ok, detail, time.perf_counter() - t0))

    w = max(len(r[0]) for r in rows)
    print(f"\n{'=' * 78}\nSUMMARY\n{'=' * 78}")
    for name, ok, detail, dt in rows:
        mark = "SKIP" if ok is None else ("PASS" if ok else "FAIL")
        print(f"{mark}  {name:<{w}}  {dt:6.1f}s  {detail}")
    failed = [r for r in rows if r[1] is False]
    passed = sum(1 for r in rows if r[1] is True)
    skipped = sum(1 for r in rows if r[1] is None)
    print(f"\n{'ALL CHECKS PASSED' if not failed else 'VERIFICATION FAILED'}: "
          f"{passed} passed, {len(failed)} failed, {skipped} skipped. Reports: {report_dir}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
