"""The light-only identity (design/BRAND.md): no dark-mode logic, AA contrast of the token pairs the CSS relies on,
status marks that never rely on colour alone, the payee-check row, and the brand mark delivered without an <img>."""
import re
from pathlib import Path

import pytest

from ui.theme import ICON, RISK, STATUS, kv_html, load_css, pill

ROOT = Path(__file__).resolve().parents[3]
TOKENS = (ROOT / "design" / "tokens.css").read_text(encoding="utf-8")


def _tokens() -> dict[str, str]:
    return dict(re.findall(r"(--[\w-]+):\s*(#[0-9a-fA-F]{6})\b", TOKENS))


def _lum(hex_: str) -> float:
    def lin(c: int) -> float:
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hex_[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contrast(fg: str, bg: str) -> float:
    hi, lo = sorted((_lum(fg), _lum(bg)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_no_dark_mode_logic_anywhere():
    for rel in ("design/tokens.css", "ui/components.css", "ui/theme.py", "ui/app.py", ".streamlit/config.toml"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "prefers-color-scheme" not in text, rel
        assert "data-theme" not in text, rel
    assert 'base = "light"' in (ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8")
    assert "color-scheme: light" in TOKENS


@pytest.mark.parametrize("fg,bg,minimum", [
    ("--label", "--bg-canvas", 7.0), ("--label-2", "--bg-canvas", 4.5), ("--label-3", "--bg-canvas", 4.5),
    ("--label-3", "--bg-surface", 4.5), ("--label-2", "--bg-sidebar", 4.5), ("--label-2", "--bg-grouped", 4.5),
    ("--accent", "--bg-surface", 4.5), ("--accent", "--bg-canvas", 4.5), ("--on-accent", "--accent-fill", 4.5),
    ("--success-text", "--bg-canvas", 4.5), ("--warning-text", "--bg-canvas", 4.5),
    ("--danger-text", "--bg-canvas", 4.5), ("--success-text", "--bg-surface", 4.5),
    ("--warning-text", "--bg-surface", 4.5), ("--danger-text", "--bg-surface", 4.5),
])
def test_token_pairs_meet_aa(fg, bg, minimum):
    t = _tokens()
    assert contrast(t[fg], t[bg]) >= minimum, (fg, bg, contrast(t[fg], t[bg]))


def test_tertiary_ink_is_aa_on_every_surface_and_secondary_still_used_on_tints():
    """--label-3 reaches AA (4.5:1) on paper, white, inset and the sidebar tone; the sidebar captions and the
    blocked tint keep --label-2 for extra margin."""
    t = _tokens()
    for bg in ("--bg-canvas", "--bg-surface", "--bg-grouped", "--bg-sidebar"):
        assert contrast(t["--label-3"], t[bg]) >= 4.5, bg
    css = (ROOT / "ui" / "components.css").read_text(encoding="utf-8")
    assert '[data-testid="stSidebar"] :is([data-testid="stCaptionContainer"]' in css
    assert 'st-key-card_blocked_"] :is([data-testid="stCaptionContainer"]' in css


def test_every_status_mark_has_an_icon_and_a_word():
    for table in (RISK, STATUS):
        for key, (icon, label, tone) in table.items():
            out = pill(icon, label, tone)
            assert icon in ICON and "<svg" in out and label in out, key
            assert "background" not in out                          # quiet by design: no filled shape inline


def test_payee_check_is_a_row_with_icon_word_and_detail():
    out = kv_html([("To", "Jean")], ['Payee check: close match - the bank has this account as "Karim Haddad". Did you mean that?'])
    assert "class='bg-kv'" in out and "<div class='k'>Payee check</div>" in out
    assert "bg-pill warn" in out and "Close match" in out and "Did you mean that?" in out
    ok = kv_html([], ['Payee check: match - the bank has this account as "Jean Dupont".'])
    assert "bg-pill ok" in ok and "Match" in ok
    evil = kv_html([], ["Payee check: no match - <img src=x onerror=alert(1)>"])
    assert "<img" not in evil and "&lt;img" in evil and "bg-pill bad" in evil


def test_brand_mark_reaches_the_page_as_a_css_variable_never_an_img():
    css = load_css()
    assert css.startswith("<style>") and "--mark-small: url(" in css and "data:image/svg+xml," in css
    assert "<img" not in css and "light" in css
