# BorderlessGuard design system (light only)

`design/tokens.css` is the single source of visual values for the Streamlit app (`ui/`), the launch video and the
demo presentation. The art direction is in `design/BRAND.md`; the mark is `design/brand/mark.svg` (and
`mark-small.svg` at 24 px and below). There is **no dark theme**: no `prefers-color-scheme`, no `data-theme`, no
dark variants. `.streamlit/config.toml` gives Streamlit's own widgets the same light colours.

Direction: paper and ink, one ultramarine accent, hairlines instead of boxes and shadows, typography carries the
hierarchy, tabular figures for money, meaning colours only for meaning. No gradients, glows, blur or coloured
shadows. The app uses the platform font (`-apple-system, BlinkMacSystemFont, "Segoe UI", Inter, sans-serif`); no
Apple fonts or logos.

## Tokens
- **Surfaces**: `--bg-canvas` paper (page), `--bg-surface` white (a decision card, the chat input, outlined buttons),
  `--bg-grouped` inset (user bubble, code blocks), `--bg-sidebar`, `--sep` / `--sep-strong` hairlines.
- **Ink**: `--label` text, `--label-2` secondary (6.7:1 on paper), `--label-3` tertiary (4.8:1 on paper and 5.2:1 on
  white; captions and tiny labels only, never on the sidebar tone or on a tint, where the app uses `--label-2`).
- **One accent**: `--accent` / `--accent-fill` ultramarine for the primary action, links, focus and the route in the
  mark. Nothing else is blue.
- **Meaning**: `--success` (one tap, done), `--warning` (code needed), `--danger` (blocked), each with `-text` (AA on
  paper) and `-tint` (a notice background). Always an icon and a word next to the colour.
- **Type scale** (size / line height): caption 12/16, footnote 13/18, subhead 14/20, body 16/24, headline 17/24,
  title3 20/28, title2 24/32, title1 32/40, display 44/48 (amount hero). Titles semibold, tracking -0.022em.
  Tiny labels may be uppercase with `--tracking-caps` (0.06 em); nothing else is.
- **Spacing**: 4-pt base, 8-pt rhythm (`--s-1` 4 ... `--s-8` 64). **Radii**: 6 / 10 / 14 / 20. **Elevation**: hairline
  rings (`--shadow-sm`); a real shadow only for floating layers (menus, popovers). **Motion**: 120 / 220 / 420 ms,
  zeroed under `prefers-reduced-motion`; nothing animates except hover and focus colour.
- **Focus**: a 2 px ultramarine outline on every control reached by keyboard; inputs always show their ring.

## Components (`ui/components.css`, helpers in `ui/theme.py`)
- **Header**: the small mark (CSS variable `--mark-small`, drawn on the `h1`; never an `<img>`) and the wordmark at 22 px,
  the sandbox status as a hairline-outlined tag at the right, the one-line pitch at 24 px (said once on the page),
  one line of secondary text, the risk legend as inline marks.
- **Status mark** (`theme.pill`): icon plus word in the meaning colour, no filled shape. Risk and status marks come from
  `theme.RISK` / `STATUS`; "Waiting for you" is neutral ink, not blue.
- **Decision card**: a white surface with a hairline ring **only while a decision is needed** (status
  `awaiting_confirmation`). Order: risk and status marks, plain title, amount hero (display size, tabular figures),
  one-sentence reason with icon, the key facts as a hairline-ruled list (`kv_html`: label left, value right; the
  Verification of Payee result is its own row with an icon and a word), the rules-passed caption, disclosure rows
  (why, technical details), then the actions: one primary (Approve / Verify and approve), the rest quiet. A card that
  needs a code carries a 3 px amber rule on its left edge. Once done, cancelled, duplicated or failed it becomes a flat
  record (a hairline above, a smaller amount); a blocked action is a notice (3 px red rule, faint red tint). The
  container key carries the phase and risk (`card_<phase>_<risk>_<id>`); CSS reads it.
- **Notices** (`st.success|info|warning|error` with a Material icon): a 3 px rule in the meaning colour, a faint tint,
  ink text. Quarantined invoice, code needed, wrong code, receipts signed with a demo key, verification results.
- **Chat**: the agent speaks in plain text (a 42 rem measure, no bubble, no avatar); the user's message is a right-aligned
  inset bubble with a hairline ring. Quick replies are small outlined pills under the last agent message.
- **Projection**: not a card; a hairline above, a status mark, two ruled figures (balance after, change, no colour),
  then the ruled lines of the simulation.
- **Lists, not grids**: examples and attacks are hairline-ruled rows (the whole row is the button, an arrow or "Run" at
  the right). Invoices to attach are quiet outlined buttons.
- **Tabs**: text with a 2 px ink underline on the selected tab and a hairline under the strip.
- **Disclosures** (`st.expander`): hairline rows without a background; the technical details are a definition list with
  monospace values; the rule list shows an icon and a word per rule.
- **Guided bar**: opaque paper (no blur), one hairline underneath, sticky under the header. Six steps as a numbered list
  with a 2 px progress rule above each (ink done, ultramarine now, red unexpected); a check or a cross replaces the
  number. One primary button for the next step, restart is quiet.
- **Receipts**: a real table (`<th scope="col">`, a caption for screen readers): hairline rows, monospace
  fingerprints, a status mark per row, and the customer's signed copy as a labelled line with its status.
- **Attack test**: one ruled row per attack with its verdict (BLOCKED / NOT BLOCKED as a tiny label with icon) and the
  details underneath once run. The score is typography ("7 / 7 blocked" at 56 px), not a badge.
- **Buttons and inputs**: 40 px, radius 10. Primary = ultramarine fill, white text (7.5:1). Secondary = white with a hairline.
  Inputs are white with a hairline and an ultramarine focus ring.
- **Untrusted text** is never rendered as HTML: every interpolated value goes through `esc_html` / `esc_md`
  (`ui/util.py`); the markup helpers never emit `<img>`. Icons are static markup in `ui/theme.py`.
- Streamlit chrome that looks unfinished (toolbar, deploy button, footer, header anchors) is hidden.

## Checks
`tests/unit/ui/` covers behaviour and escaping and, in `test_ui_light_identity.py`, the identity itself: no dark-mode
logic anywhere, AA contrast of the token pairs the CSS uses, status marks with an icon and a word, the payee-check
row. `./.venv/Scripts/python scripts/screenshots.py [out_dir] [WIDTHxHEIGHT]` drives the real backend and writes the
PNGs in `deliverables/screenshots/` (a `light` or `dark` argument is accepted and ignored).
