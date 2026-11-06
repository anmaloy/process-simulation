# Visual Style Guide — "Instrument Panel on Dark Glass"

Extracted from `edunode_app` (`ui/index.html`). This describes the **look only** —
tokens, surfaces, type, component recipes — so a new app reads as the same system
regardless of what it does. All styling is hand-written CSS with custom properties;
no framework.

> **Note for this project:** this guide is written for a web/CSS UI; our app is
> PySide6/Qt. The color tokens, type roles, and component intent carry over
> directly, but the CSS mechanics do not. See the "Visual style" section of
> `CLAUDE.md` for how these map to Qt (QSS, `QPalette`, bundled fonts, canvas
> painting) and which parts have no Qt equivalent.

---

## 1. Direction

A technical readout aesthetic: **deep slate field, one signal-cyan accent for the
"live" path, phosphor colors per status.** Everything sits on dark translucent
glass with soft glows, not flat cards. Two typefaces do all the work — a geometric
display sans for everything readable, a mono for every id, label, code, and metric
so machine tokens look like machine tokens.

Mood words that drive every decision: *instrument panel, star-chart, phosphor
readout, dark glass.* When in doubt: darker surface, thinner border, one cyan accent,
mono for anything that is data.

---

## 2. Color tokens

Drop this `:root` in verbatim. These are the whole palette — do not introduce new
hues; extend by reusing roles.

```css
:root{
  /* field — four stacked dark surfaces, each ~1 step lighter */
  --bg:#0b0f17;        /* page base (also PWA theme-color) */
  --bg-2:#10151f;      /* inset wells: code blocks, sidebar top */
  --panel:#141b27;     /* raised surface 1 */
  --panel-2:#1a2331;   /* raised surface 2 (chips, inputs, inner rows) */

  /* hairlines — borders only, two weights */
  --line:#26303f;      /* default divider / border */
  --line-2:#33425a;    /* stronger border, hover edges, scrollbar thumb */

  /* ink — text, three dim levels */
  --ink:#e8edf5;       /* primary text */
  --ink-dim:#9aa7ba;   /* secondary / body copy */
  --ink-faint:#5f6e85; /* captions, ids, disabled */

  /* the one accent — "signal cyan", the active spine */
  --spine:#34d3e0;
  --spine-glow:rgba(52,211,224,.35);

  /* status phosphors — bright, for dots/accents/legend */
  --s-not:#566072;     /* grey   — not started */
  --s-learn:#e3b53e;   /* yellow — learning / warning */
  --s-ref:#2f9e6b;     /* green  — done / go */
  --s-indep:#3d6fd6;   /* blue   — answer / independent */
  --s-expert:#8b5cf6;  /* violet */
  --s-teach:#d9e0ec;   /* silver — top / mastery */

  /* status FILLS — darker twins, sit behind white label text */
  --f-not:#1c2433; --f-learn:#5e4a16; --f-ref:#16513a;
  --f-indep:#1d3a78; --f-expert:#3a2a6b; --f-teach:#6c7585;

  --subedge:#d9a93a;   /* secondary accent — gold, for a second line type */

  --radius:10px;
  --mono:'IBM Plex Mono',ui-monospace,monospace;
  --disp:'Space Grotesk',system-ui,sans-serif;
}
```

**Rules**
- **One accent.** `--spine` (cyan) marks whatever is active/live/selected. Gold
  (`--subedge`) is the only secondary accent, reserved for a distinct second element
  class — never used just for variety.
- **Bright vs fill pairs.** Each status has a bright phosphor (`--s-*`) for dots and
  text, and a dark fill (`--f-*`) for areas that carry white label text on top. Pick
  the bright one for a 6–9px dot or colored text; the fill for a filled shape with a
  label inside.
- **Semantic reuse:** green = success/go, yellow = in-progress/warning, blue = a
  secondary reveal, cyan = the primary interactive accent.

---

## 3. Typography

```css
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap');
```

Two families, strict division of labor:

| Use | Family | Treatment |
|---|---|---|
| Headings, body, buttons | **Space Grotesk** (`--disp`) | weight 600 for headings, `letter-spacing:-.01em` on larger sizes |
| **Every id / code / label / metric / count** | **IBM Plex Mono** (`--mono`) | small, often uppercase + wide tracking |

**The signature move — the "eyebrow / role" label.** Small caps mono over every
section and card title:

```css
font-family:var(--mono); font-size:10px; letter-spacing:.16em;
text-transform:uppercase; color:var(--spine);   /* or --ink-faint for quieter ones */
```

Tracking scales with smallness: `.22em` at 10.5px, `.16em` at 10px, `.08em` at 13px.
The tighter the type is to display, the looser the tracking.

**Size ladder (actual values in use):**
- Big view title: 24px / 600 / `-.015em`
- Card title (`h2`): 15–17px / 600
- Body / description: 12.5–13px / line-height 1.5
- Long-form reading doc: 15px / line-height **1.7**, body color `#d3dae6`
- UI control / mono label: 10–11px
- Micro caption / id: 9–10.5px, `--ink-faint`

Body uses `--disp` and `-webkit-font-smoothing:antialiased`.

---

## 4. Surfaces, elevation, glass

Depth is built from the four `--bg`/`--panel` steps plus **translucency + blur** for
anything floating.

```css
body{
  background:
    radial-gradient(1200px 800px at 75% -10%, #131c2b 0%, transparent 60%),
    var(--bg);
}
```

A single off-screen top-right radial glow lifts the whole field — reuse it verbatim
as the app background and on full-screen overlays.

**Floating panels = dark glass:** semi-transparent panel color + backdrop blur +
hairline + big soft shadow.

```css
.panel-glass{
  background:rgba(16,21,31,.92);
  backdrop-filter:blur(14px);
  border:1px solid var(--line-2);
  border-radius:14px;
  box-shadow:0 18px 50px -20px #000;   /* deep, soft, downward */
}
```

Blur/opacity scale with prominence: toolbar `.82`/`blur(10px)`, legend `.8`/`blur(8px)`,
detail & pathway cards `.92–.94`/`blur(14px)`. Fixed chrome is glass; solid structural
regions (sidebar, headers) use the opaque `--panel` steps.

**Radius ladder:** 4–6px (tags, inner buttons) · 8px (buttons, inputs, inner rows) ·
10px (`--radius`, small cards, legend) · 12–14px (large floating cards). Pills are
`20px`/full. Nothing is sharp-cornered except full-bleed dividers.

**Borders are hairlines, never heavy.** 1px `--line` default; `--line-2` for hover or
a stronger card edge. Hover generally means *brighten the border to cyan*, not fill.

---

## 5. Layout grammar

A **rail + stage** shell, CSS grid, fixed-width rail:

```css
.shell{display:grid;grid-template-columns:300px 1fr;height:100vh}
```

- **Left rail (300px):** opaque vertical gradient `linear-gradient(180deg,var(--bg-2),var(--bg))`,
  right hairline border. Holds a brand block (eyebrow + title + mono subtitle) over a
  scrolling list.
- **Main stage:** `position:relative; overflow:hidden`. Floating chrome is absolutely
  positioned over it — centered toolbar pill at top, legend bottom-left, detail card
  bottom-right, panels slide in from edges.
- **Full-screen secondary view** reuses the identical grid (`300px 1fr`) and the same
  background gradient, layered above via `position:fixed; inset:0; z-index:60`.

**Responsive:** single breakpoint at `max-width:820px`. Rail collapses to an off-canvas
drawer (`transform:translateX(-100%)`, `.open` slides it in), a `☰` menu button appears,
legend hides, content padding drops from 40px to 16px.

---

## 6. Component recipes

### Segmented control (primary toggle pattern)
A mono-labeled group in an inset well; active segment goes solid cyan.

```css
.seg{display:flex;background:var(--bg);border-radius:8px;padding:3px;gap:2px}
.seg button{font-family:var(--mono);font-size:11px;letter-spacing:.04em;
  color:var(--ink-dim);background:none;border:0;padding:6px 12px;border-radius:6px;cursor:pointer}
.seg button.on{background:var(--spine);color:var(--bg);font-weight:500}
```

### Tool button (standalone)
```css
.tool-btn{font-family:var(--mono);font-size:11px;color:var(--ink-dim);
  background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:6px 10px;cursor:pointer}
.tool-btn:hover{color:var(--ink);border-color:var(--line-2)}
.tool-btn.on{background:var(--spine);color:var(--bg);border-color:var(--spine);font-weight:600}
```

### Pills & chips
- **Pill** (status/metadata): mono 10px, `border-radius:20px`, hairline border; a
  "status" variant drops the border and fills with color, text `--bg`.
- **Chip** (clickable reference): 11px, `--panel-2` bg, hairline; **hover brightens
  border to cyan + text to `--ink`**.

### List leaf (rail rows)
Left-border accent pattern — the selection indicator is a 2px left border that lights cyan:
```css
.leaf{padding:5px 18px 5px 22px;border-left:2px solid transparent;cursor:pointer}
.leaf:hover{background:var(--panel);border-left-color:var(--line-2)}
.leaf.active{background:var(--panel-2);border-left-color:var(--spine)}
```
Each row leads with a **status dot**: `width:8px;height:8px;border-radius:50%` + inset
dark ring `box-shadow:0 0 0 1px rgba(0,0,0,.3) inset`.

### Collapsible band / section
Header row (mono, uppercase, a colored dot, a count pushed right, a caret that rotates
90° when open), body hidden until `.open`. Caret: `transition:transform .12s`.

### Inputs
```css
input{font-family:var(--mono);font-size:11.5px;padding:8px 10px;border-radius:8px;
  background:var(--panel-2);border:1px solid var(--line-2);color:var(--ink);outline:none}
input:focus{border-color:var(--spine)}       /* focus = cyan border, no glow ring */
```
`accent-color:var(--spine)` on checkboxes/radios.

### Meter / progress
```css
.meter{height:5px;border-radius:3px;background:var(--panel-2);overflow:hidden}
.meter i{display:block;height:100%;background:var(--spine);border-radius:3px;transition:width .3s}
```

### Checkbox mark (custom)
15px rounded-square, 1.5px faint border; when done fills green and reveals a `✓` via
opacity. Diamond variant = same box `transform:rotate(45deg)` for a different item class.

### Callout / note
Left-accent block, tinted wash of the accent at ~5–8% alpha, squared-off left corners:
```css
.note{border-left:2px solid var(--spine);background:var(--panel);border-radius:0 10px 10px 0;padding:10px 16px}
/* warning variant: --s-learn + rgba(227,181,62,.05); answer variant: --s-indep + rgba(61,111,214,.06) */
```

### Long-form reading surface
Centered `max-width:860px` column, generous padding, `line-height:1.7`. Headings get a
top hairline rule + padding; `h4` is the mono uppercase eyebrow. Code inline =
`--panel-2` chip; `pre` = `--bg-2` well. Tables: no outer border, only `border-bottom`
hairlines per row, mono uppercase `th`.

---

## 7. Motion

Fast and understated. Standard transitions **.12s** (color/border) to **.18s**
(carets/chevrons). Panel slide-ins **.26s** with `cubic-bezier(.2,.8,.25,1)`
(decelerate). Panels enter via `transform` off-screen (`translateY(140%)`,
`translateX(-130%)`) and land at `transform:none` on a `.show` class.

Two decorative loops, both gated by `prefers-reduced-motion`:
- marching dashes on the gold line type (`stroke-dashoffset` keyframe, direction = meaning)
- a shake on a wrong answer (`translateX` ±4px over .32s)

```css
@media (prefers-reduced-motion:reduce){ .subedge{animation:none} .qcard.miss{animation:none} }
```

---

## 8. Glow & accent effects

The cyan accent **glows**; nothing else does.
```css
filter:drop-shadow(0 0 6px var(--spine-glow));   /* hover on an active element */
box-shadow:0 0 22px var(--spine-glow), inset 0 0 18px rgba(52,211,224,.08);  /* active frame */
```
Selection/hover escalates stroke width *and* adds the glow; dimmed/inactive elements
drop to `opacity:.16–.5`. Custom scrollbars everywhere: thin, thumb `--line-2`,
transparent track, often inset via `border:3px solid transparent;background-clip:content-box`.

---

## 9. Iconography & brand mark

- **Glyphs are plain Unicode**, not an icon font: `☰ ✕ ⟶ ✓ ◆` and caret chars. Keep it that way.
- **App icon** = the system in miniature: `#0b0f17` rounded-square (`rx:112/512`), a
  small node-graph of cyan hairline edges (`stroke-opacity .6`) connecting five circles
  colored in the status phosphors, cyan node largest at top. Any new app's mark should
  restate its own content as this same dark-tile + phosphor-nodes + cyan-edges motif.
- **Brand block** (top of rail): mono cyan eyebrow → 600-weight title → mono faint subtitle.

---

## 10. Minimal starter

Smallest skeleton that already looks like this system — paste the `:root` from §2, the
`@import` from §3, then:

```css
*{box-sizing:border-box}
html,body{height:100%}
body{
  margin:0; color:var(--ink); font-family:var(--disp);
  -webkit-font-smoothing:antialiased;
  background:radial-gradient(1200px 800px at 75% -10%, #131c2b 0%, transparent 60%), var(--bg);
}
h1,h2,h3{font-weight:600;letter-spacing:-.01em}
.eyebrow{font-family:var(--mono);font-size:10px;letter-spacing:.16em;
  text-transform:uppercase;color:var(--spine);margin:0 0 6px}
```

Then build with: four stacked surfaces, hairline borders, mono for all data, one cyan
accent (glowing) + gold second accent, glass for anything that floats, left-border
accents for selection, .12s transitions, a single 820px breakpoint.
