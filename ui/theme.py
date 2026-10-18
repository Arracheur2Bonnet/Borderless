"""Visual layer helpers: loads the shared design tokens, inline icons, pills. No untrusted text is handled here
except through esc_html (pill labels)."""
from __future__ import annotations

from pathlib import Path

from ui.util import esc_html

ROOT = Path(__file__).resolve().parents[1]


def load_css() -> str:
    tokens = (ROOT / "design" / "tokens.css").read_text(encoding="utf-8")
    comps = (Path(__file__).parent / "components.css").read_text(encoding="utf-8")
    return f"<style>\n{tokens}\n{comps}\n</style>"


def _svg(body: str, fill: bool = False) -> str:
    attrs = ('fill="currentColor"' if fill else 'fill="none" stroke="currentColor" stroke-width="2.2" '
             'stroke-linecap="round" stroke-linejoin="round"')
    return f'<svg viewBox="0 0 24 24" {attrs} aria-hidden="true">{body}</svg>'


# Static, trusted markup (never built from user text).
ICON = {
    "check": _svg('<path d="M5 12.5l4.5 4.5L19 7.5"/>'),
    "x": _svg('<path d="M6 6l12 12M18 6L6 18"/>'),
    "key": _svg('<circle cx="8" cy="15" r="4"/><path d="M11 12l8-8M16 7l3 3"/>'),
    "block": _svg('<circle cx="12" cy="12" r="9"/><path d="M5.6 5.6l12.8 12.8"/>'),
    "eye": _svg('<path d="M2 12s3.7-7 10-7 10 7 10 7-3.7 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>'),
    "undo": _svg('<path d="M9 14L4 9l5-5"/><path d="M4 9h10a6 6 0 010 12h-3"/>'),
    "clock": _svg('<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>'),
    "repeat": _svg('<path d="M17 2l4 4-4 4"/><path d="M3 11V9a3 3 0 013-3h15"/><path d="M7 22l-4-4 4-4"/>'
                   '<path d="M21 13v2a3 3 0 01-3 3H3"/>'),
    "lock": _svg('<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 018 0v3"/>'),
    "shield": _svg('<path d="M12 3l8 3v6c0 4.5-3.2 8.2-8 9-4.8-.8-8-4.5-8-9V6l8-3z"/>'),
    "shield_check": _svg('<path d="M12 3l8 3v6c0 4.5-3.2 8.2-8 9-4.8-.8-8-4.5-8-9V6l8-3z"/><path d="M8.5 12l2.5 2.5L15.5 9.5"/>'),
    "alert": _svg('<path d="M12 4l9 16H3L12 4z"/><path d="M12 10v4M12 17.5v.01"/>'),
    "chart": _svg('<path d="M4 20V10M10 20V4M16 20v-8M22 20H2"/>'),
    "play": _svg('<path d="M7 4.5v15l12-7.5-12-7.5z"/>', fill=True),
    "flask": _svg('<path d="M9 3h6M10 3v6l-5.5 9.5A1.5 1.5 0 005.8 21h12.4a1.5 1.5 0 001.3-2.5L14 9V3"/>'),
    "receipt": _svg('<path d="M6 3h12v18l-3-2-3 2-3-2-3 2V3z"/><path d="M9 8h6M9 12h6"/>'),
    "hand": _svg('<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3z"/>'),
}

# risk level -> (icon, label, tone)
RISK = {
    "R0": ("eye", "Read-only", "neutral"), "R1": ("undo", "Reversible", "info"), "R2": ("check", "One tap", "ok"),
    "R3": ("key", "Code needed", "warn"), "R4": ("block", "Blocked", "bad"),
}
STATUS = {
    "awaiting_confirmation": ("clock", "Waiting for you", "info"), "executed": ("check", "Done", "ok"),
    "denied": ("block", "Blocked", "bad"), "rejected_by_user": ("x", "Cancelled", "neutral"),
    "replayed": ("repeat", "Duplicate ignored", "neutral"), "failed": ("x", "Failed", "bad"),
    "proposed": ("clock", "Proposed", "neutral"),
}
TONE_OF_RISK = {"R0": "neutral", "R1": "info", "R2": "ok", "R3": "warn", "R4": "bad"}


def pill(icon: str, text: str, tone: str = "neutral", extra: str = "") -> str:
    return f"<span class='bg-pill {tone} {extra}'>{ICON.get(icon, '')}{esc_html(text)}</span>"
