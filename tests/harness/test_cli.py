"""P1-05: the scripted-task CLI replays mission T01 (golden path) offline and handles bad input."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "scripts" / "agent_cli.py"
T01 = ROOT / "tests" / "harness" / "tasks_T01.jsonl"


def run_cli(*args, stdin=None):
    env = {k: v for k, v in __import__("os").environ.items() if not k.startswith("BG_")}
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.run([sys.executable, str(CLI), *args], input=stdin, capture_output=True, text=True,
                       encoding="utf-8", cwd=ROOT, env=env, timeout=120)
    return p, [json.loads(line) for line in p.stdout.splitlines() if line.strip()]


def test_t01_through_the_cli_ends_at_600_196_74_and_three_receipts():
    p, out = run_cli("run", str(T01))
    assert p.returncode == 0, p.stderr
    assert [r["op"] for r in out] == ["message", "confirm", "confirm", "confirm", "overview"]
    first = out[0]
    assert [a["type"] for a in first["actions"]] == ["fx", "payment", "payment"]
    assert [a["status"] for a in first["actions"]] == ["awaiting_confirmation"] * 3
    assert any(e["kind"] == "prompt_injection" for e in first["events"])
    assert "Cost of this exchange: 11.96 USD (0.50%)" in first["assistant_text"]
    assert [r["receipts"] for r in out[1:4]] == [["rcpt_0001"], ["rcpt_0002"], ["rcpt_0003"]]
    last = out[-1]
    assert last["balances"] == {"EUR": "600.00", "USD": "196.74"}
    assert last["receipt_ids"] == ["rcpt_0001", "rcpt_0002", "rcpt_0003"] and last["chain_valid"] is True
    assert out[3]["actions"][0]["verdict"] == "step_up" and out[3]["actions"][0]["status"] == "executed"
    assert "246810" not in p.stdout                      # the one-time code never comes back


def test_output_has_the_documented_fields_and_no_full_iban():
    _, out = run_cli("run", str(T01))
    for key in ("assistant_text", "actions", "events", "balances", "receipts"):
        assert key in out[0]
    for key in ("id", "type", "title", "status", "risk", "verdict", "failed_rules"):
        assert key in out[0]["actions"][0]
    blob = json.dumps(out)
    assert "FR76" not in blob and "GB" + "33" not in blob and "****0143" in blob


def test_tasks_from_stdin_and_output_file(tmp_path):
    tasks = '{"session":"a","op":"overview"}\n\n# comment\n{"session":"a","op":"message","text":"what is my balance?"}\n'
    out_file = tmp_path / "o.jsonl"
    p, _ = run_cli("run", "-", "-o", str(out_file), stdin=tasks)
    rows = [json.loads(line) for line in out_file.read_text(encoding="utf-8").splitlines()]
    assert p.returncode == 0 and len(rows) == 2 and "450.00 EUR" in rows[1]["assistant_text"]


def test_bad_lines_are_reported_and_the_run_continues():
    tasks = "\n".join([
        "not json",
        '{"session":"a","op":"fly"}',
        '{"op":"message","text":"hi"}',
        '{"session":"a","op":"message","text":42}',
        '{"session":"a","user":"u_nobody","op":"message","text":"hi"}',
        '{"session":"b","op":"confirm","action":"last"}',
        '{"session":"b","op":"confirm","action":"act_nope"}',
        '[1,2]',
        '{"session":"b","op":"message","text":"hello"}'])
    p, out = run_cli("run", "-", stdin=tasks)
    assert p.returncode == 0
    assert [r["ok"] for r in out] == [False] * 8 + [True]
    assert "not valid JSON" in out[0]["error"] and "unknown op" in out[1]["error"]
    assert out[-1]["actions"] == [] and "BorderlessGuard" in out[-1]["assistant_text"]
    p, _ = run_cli("run", "-", "--strict", stdin="not json\n")
    assert p.returncode == 1


def test_wrong_otp_keeps_the_step_waiting_and_a_second_user_cannot_see_the_first():
    tasks = "\n".join([
        '{"session":"x","user":"u_lina","op":"message","text":"send 100 euros to jean"}',
        '{"session":"x","op":"confirm","action":"last","otp":"000000"}',
        '{"session":"x","op":"confirm","action":"last","otp":"246810"}',
        '{"session":"y","user":"u_other","op":"overview"}'])
    _, out = run_cli("run", "-", stdin=tasks)
    assert out[0]["actions"][0]["needs_otp"] is True
    assert out[1]["actions"][0]["status"] == "awaiting_confirmation"
    assert any(e["kind"] == "step_up_failed" for e in out[1]["events"])
    assert out[2]["actions"][0]["status"] == "executed" and out[2]["balances"]["EUR"] == "350.00"
    assert out[3]["balances"] == {"EUR": "9800.00"} and out[3]["receipt_ids"] == []


def test_a_recurring_request_creates_nothing_through_the_cli():
    _, out = run_cli("run", "-", stdin='{"session":"a","op":"message","text":"send 100 euros to mom every month"}\n')
    assert out[0]["actions"] == [] and "can't schedule" in out[0]["assistant_text"]


def test_the_default_parser_ignores_the_environment():
    env = dict(__import__("os").environ, BG_PARSER="local", BG_LOCAL_MODEL="nonexistent")
    p = subprocess.run([sys.executable, str(CLI), "run", "-"], input='{"session":"a","op":"message","text":"hello"}\n',
                       capture_output=True, text=True, encoding="utf-8", cwd=ROOT, env=env, timeout=60)
    assert p.returncode == 0 and "BorderlessGuard" in json.loads(p.stdout)["assistant_text"]


def test_connected_parser_without_configuration_is_refused():
    p, _ = run_cli("run", "-", "--parser", "connected", stdin="")
    assert p.returncode != 0 and "could not be built" in (p.stderr + p.stdout)


def test_chat_mode_reads_stdin_until_quit():
    p = subprocess.run([sys.executable, str(CLI), "chat", "--parser", "rules"], input="hello\n/overview\n/quit\n",
                       capture_output=True, text=True, encoding="utf-8", cwd=ROOT, timeout=60)
    assert p.returncode == 0 and "BorderlessGuard" in p.stdout and "balances" in p.stdout
