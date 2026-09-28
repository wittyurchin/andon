# Product

## Register

product

## Users

Three people use the same screen, in this order of priority:

1. **Operations control room.** Someone on a central ops team watching kitchens on a large monitor through the day, glancing over between other work. Needs the state of a kitchen in one look, and needs to notice when it changes.
2. **Kitchen manager on a phone.** Checking one kitchen during service, often in a bright, noisy kitchen or outdoors. Needs one answer fast: is anything going to hurt my riders or my orders right now?
3. **Analyst at a desk.** Studying the sources: which API said what, when, from how far away, and why the system concluded what it did. Tunes thresholds and decides which providers to trust.

The job: know what is happening around a kitchen right now, how it is changing, and what it means operationally, without being misled by a source that is wrong, stale, far away, or silent.

## Product Purpose

A situation-awareness system for food-delivery kitchens. It gathers weather (station, model, radar, official alerts), traffic on the roads that actually lead to the kitchen, and local incidents; keeps full provenance for every reading; and turns them into one honest assessment.

Success is when a user trusts the assessment *because* they can see its working: which sources agree, which disagree, what is measured versus modelled versus inferred, how old each reading is, and what the system cannot see. A situation screen that looks confident while a source is dead has failed.

## Brand Personality

**Precise, calm, honest.**

A scientific instrument, not a news ticker. Quiet by default; color and motion appear only when something matters. Plain language that never overclaims: "a model estimates", "the station 4 km away reported", "no feed covers waterlogging here". Shows its working without making the user dig.

References: Windy (weather drawn where it is, spatial first) and Flightradar (a live operational board, many moving parts, status at a glance).

## Anti-references

- **Generic SaaS admin.** No grid of identical metric cards, no hero number with a gradient accent, no dashboard template look.
- **Consumer weather app.** No cute suns and clouds, no sky gradients, no playful icons. This is not a forecast for someone's weekend.
- Anything that makes a guess look like a fact, or makes missing data look like "all clear".

## Design Principles

1. **Measured, modelled, inferred are three different things, and look it.** Every reading carries its epistemic status visually, consistently, everywhere it appears. An observation must never be confusable with a model estimate.
2. **Silence is information.** A dead, stale, unauthorised or disabled source is shown as clearly as a healthy one. Absence of data is never drawn as calm.
3. **Place before number.** A reading 60 km away is not a reading here. Distance, direction and relevance to the kitchen travel with every value.
4. **Disagreement is the story.** When sources disagree, that is the headline, not a footnote. Never average it away.
5. **One glance, then depth.** The answer is readable from across a room; the provenance is one interaction away, never more.

## Accessibility & Inclusion

- WCAG AA contrast for all text in the chosen theme.
- Severity and epistemic status are never conveyed by color alone: always paired with a label, shape, or pattern.
- Respect `prefers-reduced-motion`.
- Readable on a phone in bright light and on a large monitor from a distance.
