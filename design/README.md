# BorderlessGuard design system

`design/tokens.css` is the single source of visual values for the Streamlit app (`ui/`) and, later, the keynote.
Direction: Apple HIG-inspired. Restraint, clear hierarchy, generous space, one accent, real content over decoration.
System fonts only (`-apple-system, BlinkMacSystemFont, "Segoe UI", Inter, sans-serif`); no Apple fonts or logos.

## Tokens
- **Colour roles** (light default; dark via `prefers-color-scheme`, or force with `data-theme="light|dark"` on `:root`):
  `--bg-canvas` page, `--bg-surface` elevated cards and bubbles, `--bg-grouped` inset groups, `--bg-material`
  translucent bar (header, guided bar, with `--blur-material`), `--sep` hairlines, `--label` / `--label-2` /
  `--label-3` text, one accent (`--accent` for text, `--accent-fill` for filled buttons).
- **Semantic**: `--success`, `--warning`, `--danger`, `--info`, each with `-text` (AA on its tint) and `-tint`
  (pill and callout background). Meaning is never colour alone: every pill carries an icon and a word.
- **Type scale** (size / line height / weight): caption 12/16, footnote 13/18, subhead 14/20, body 16/24,
  headline 17/24 semibold, title3 20/28, title2 24/32, title1 32/40, display 44/48 (amount hero).
- **Spacing**: 4-pt base, 8-pt rhythm: `--s-1` 4 ... `--s-8` 64. **Radii**: 8, 12, 18, 24, pill.
  **Shadows**: sm, md, lg. **Motion**: 120 / 220 / 400 ms with standard and emphasized easings, zeroed under
  `prefers-reduced-motion`. **Focus**: `--focus-ring`, keyboard only.

## Component rules (`ui/components.css`, helpers in `ui/theme.py`)
- **Pill**: icon + text, tinted background, semantic text colour. Risk and status pills come from `theme.RISK` / `STATUS`.
- **Action card**: one component. Order: risk and status pills, plain title, amount hero, one-sentence reason with
  icon, key facts group, disclosure rows (why, technical), then the actions (one primary, others secondary).
  Blocked cards get a faint danger tint, code-needed cards a faint warning edge, cancelled cards are muted.
- **Chat**: user right in an accent bubble, agent left in a surface bubble with a small shield avatar. Cards and
  quick replies sit under the agent bubble.
- **Guided bar**: translucent material, sticky; stepper with number, check (done) or cross (unexpected) per step.
- **Buttons**: 44 px, radius 12. Primary = accent fill, secondary = surface with hairline, quick replies = accent-tint pills.
- **Untrusted text** is never rendered as HTML: every interpolated value goes through `esc_html` / `esc_md`
  (`ui/util.py`). Icons are static markup in `ui/theme.py`.
- Streamlit chrome that looks unfinished (toolbar, deploy button, footer, header anchors) is hidden;
  `.streamlit/config.toml` gives native widgets the same light and dark colours.
