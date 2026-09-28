---
name: Andon
description: A calm, honest instrument for what is happening around a kitchen.
colors:
  paper: "oklch(97.3% 0.006 95)"
  panel: "oklch(99.3% 0.003 95)"
  sunk: "oklch(95.4% 0.007 95)"
  line: "oklch(88.5% 0.009 95)"
  line-soft: "oklch(92.6% 0.007 95)"
  ink: "oklch(23% 0.012 95)"
  ink-2: "oklch(41% 0.012 95)"
  ink-3: "oklch(51% 0.011 95)"
  ink-4: "oklch(70% 0.009 95)"
  low: "oklch(84% 0.15 96)"
  low-ink: "oklch(45% 0.1 88)"
  low-wash: "oklch(96.4% 0.045 96)"
  medium: "oklch(77% 0.155 70)"
  medium-ink: "oklch(47% 0.12 58)"
  medium-wash: "oklch(95.8% 0.04 72)"
  high: "oklch(66% 0.175 44)"
  high-ink: "oklch(47% 0.15 40)"
  high-wash: "oklch(95.2% 0.035 45)"
  severe: "oklch(55% 0.2 27)"
  severe-ink: "oklch(45% 0.18 27)"
  severe-wash: "oklch(94.6% 0.032 25)"
  ok: "oklch(58% 0.12 158)"
  ok-ink: "oklch(42% 0.1 158)"
typography:
  verdict:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, Roboto, sans-serif"
    fontSize: "2.5rem"
    fontWeight: 700
    lineHeight: 1.05
    letterSpacing: "-0.025em"
  title:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, Roboto, sans-serif"
    fontSize: "1.625rem"
    fontWeight: 650
    lineHeight: 1.2
  body:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, Roboto, sans-serif"
    fontSize: "0.9375rem"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, Roboto, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 600
    letterSpacing: "0.09em"
  raw:
    fontFamily: "ui-monospace, 'SF Mono', SFMono-Regular, Menlo, Consolas, monospace"
    fontSize: "0.75rem"
rounded:
  sm: "6px"
  md: "10px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "24px"
  2xl: "32px"
  3xl: "48px"
components:
  button-primary:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.panel}"
    rounded: "{rounded.sm}"
    padding: "8px 14px"
  button-primary-hover:
    backgroundColor: "{colors.ink-2}"
  button:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    padding: "8px 14px"
  panel:
    backgroundColor: "{colors.panel}"
    rounded: "{rounded.md}"
    padding: "16px 24px 24px"
  source-card:
    backgroundColor: "{colors.panel}"
    rounded: "{rounded.sm}"
    padding: "12px"
  source-card-down:
    backgroundColor: "{colors.severe-wash}"
  verdict-high:
    backgroundColor: "{colors.high-wash}"
    textColor: "{colors.high-ink}"
    rounded: "{rounded.md}"
    padding: "24px"
---

# Design System: Andon

## 1. Overview

**Creative North Star: "The chart recorder in a quiet control room."**

A light instrument on faint chart-paper neutrals. It is quiet until something is wrong; then colour appears, always next to a shape or a word. Three people read the same screen: an ops lead across a daylit room, a kitchen manager on a phone in a bright kitchen, an analyst at a desk. The top of the page answers "how bad" (the andon stack and level) and "where" (the site compass) in one glance; provenance is one click away.

The whole system rests on three pieces of shared vocabulary, used identically in every section, every table and the drawer:

- **Marks, how we know it:** ● measured · ○ model (estimate of now) · ◌ forecast (dashed) · ◇ inferred · ▨ simulated (hatched).
- **Lamps, is the source working:** lit (healthy) · half (stale, degraded) · crossed (unavailable, key rejected) · dashed (not configured, disabled).
- **Pips, how bad:** 0 to 4 rising bars, clear to severe, with the word beside them.

**Key Characteristics:**
- Light theme, forced by the scene (daylit rooms, bright kitchens), not by habit.
- Colour means severity or health, nothing else. "Clear" is quiet ink, not green.
- Raw provider text (METAR strings, source errors) in monospace; our own wording in the sans.
- Absence is drawn as absence: dashed outlines and "unknown, not clear", never calm.

## 2. Colors: The Chart-Paper Palette

Neutrals are tinted toward hue 95 (a faint chart-paper cast, chroma ≤0.012). The only chromatic families are the severity ramp and the health lamp.

### Neutrals
- **Paper** (oklch(97.3% 0.006 95)): the page.
- **Panel** (oklch(99.3% 0.003 95)): raised surfaces, cards, drawer.
- **Sunk** (oklch(95.4% 0.007 95)): chips, covered-capability tags, report confidence strip.
- **Ink / Ink 2 / Ink 3** (23% / 41% / 51%): primary text, secondary text, labels and meta. All pass AA on paper, panel and every wash (lowest measured: Ink 3 on severe wash, 4.83:1).
- **Ink 4** (70%): decoration only (rules, dashed "unknown" outlines, off-road incidents). Never text.

### Severity ramp (after IMD's watch / alert / warning colours)
Each level has three tones: a **fill** for pips, bars and map marks, an **ink** for words (≥6:1 on its own wash), and a **wash** for surfaces. Lightness steps down with severity, so the ramp still reads in greyscale.
- **Low** (fill 84% / 96 hue): IMD yellow, "watch".
- **Medium** (77% / 70): amber.
- **High** (66% / 44): orange.
- **Severe** (55% / 27): red.

### Health
- **Ok** (oklch(58% 0.12 158)): the lit lamp and fresh freshness segments only.
- Stale/degraded borrow Medium; down borrows Severe.

### Named Rules
**The One-Meaning Rule.** A colour is either severity or health. No decorative colour, no chromatic accent. The primary button is ink on panel.

**The Never-Alone Rule.** Every coloured element carries a shape (pips, lamp glyph, mark) or a word. Colour-blind users lose nothing.

## 3. Typography

**Font:** the system UI stack for everything; the system monospace for raw provider text, coordinates and fingerprints.

Fixed rem scale, ratio ~1.2 to 1.25: 12 / 13 / 14 / 15 (body) / 16 / 20 / 26 / 40.

- **Verdict** (40px, 700, -0.025em): the situation level only, in the level's ink.
- **Title** (26px, 650): restaurant name, drawer title.
- **Section** (16px, 650): panel titles.
- **Body** (15px, 400, 1.5): UI text. Prose (report summary, outlook, source info) runs at 16px, 1.6, capped at 70 to 75ch.
- **Label** (12px, 600, uppercase, 0.09em): eyebrows, column heads, stat names.
- **Raw** (mono, 12px): METAR strings, source error text, fingerprints. Its job is to say "this is the provider's text, not ours".

All numbers use tabular figures.

## 4. Elevation

Flat by default: depth comes from the paper/panel step and 1px lines. One shadow exists, used only on hover for clickable source cards and on the setup form: `0 1px 2px` plus a soft `0 4px 16px`, both at ≤6% opacity. The drawer casts a single left shadow. Motion is state only: 160ms hover transitions, a 220ms drawer slide, all ease-out (quart-like), all removed under `prefers-reduced-motion`.

## 5. Components

- **Andon stack.** Four stacked lamps (low to severe from the bottom), lit up to the current level, beside the verdict word. The product's name, used as its central status graphic.
- **Site compass.** SVG, kitchen at the centre. Rings are the backend's applicability bands (150 m, 1 km, 5 km, 30 km), equal width per band. Access roads are spokes coloured by corridor severity; dashed when nothing reports on them. Weather sources plotted where their reading was taken (solid station, hollow model); radar cells as squares; incidents as triangles, full size and orange only when on an access road. Items with no location are listed under it, never guessed onto the map.
- **Source card.** Kind + lamp on the top row, name, mark, reading, where it was taken, a coverage strip (Rain Wind Gust Vis Temp Fcst Alerts: filled when covered, struck through when not, amber outline when reported but unusable), then freshness meter and counts. Down sources get the severe wash; unconfigured sources a dashed frame on paper. Weather cards stay on one row and scroll sideways.
- **Signal card.** Pips top right, mark, headline, trend, a two-column facet grid. Medium and above take their level's wash. A down source is a dashed card with its reason in mono.
- **Disagreement.** An ink "Sources disagree" tag leading a full-width statement above the cards. Never a footnote.
- **Tables.** Uppercase label heads, 1px soft rules, stale rows in Ink 3, raw text in mono under the derived value. Wrapped in a horizontal scroller with a 640px minimum on phones.
- **Drawer.** Right sheet, Details and Info tabs, key-value lists at 160px label width.

## 6. Do's and Don'ts

### Do:
- **Do** use the shared `Mark`, `Lamp` and `Pips` components (`frontend/src/components/marks.tsx`) for every epistemic, health and severity signal.
- **Do** show where a reading came from next to its value.
- **Do** draw missing data as dashed and say "unknown, not clear".
- **Do** keep provider text in mono and our wording in the sans.

### Don't:
- **Don't** use colour for anything but severity and health; no brand accent.
- **Don't** show "clear" as green; clear is quiet ink.
- **Don't** add side-stripe borders, gradient text, glass, or a hero-number template.
- **Don't** use pictograms or emoji for weather (the consumer-weather-app anti-reference); use words.
- **Don't** hide, collapse or demote an existing section to make room: every section stays visible, in order.
- **Don't** put a location on the compass that the source did not report.
