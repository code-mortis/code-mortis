---
name: "Code Mortis"
description: "A compact workspace for traceable code reviews."
colors:
  background: "#f7f8f5"
  foreground: "#202c26"
  card: "#ffffff"
  card-foreground: "#202c26"
  popover: "#ffffff"
  popover-foreground: "#202c26"
  primary: "#245944"
  primary-foreground: "#ffffff"
  secondary: "#e9eee8"
  secondary-foreground: "#254533"
  muted: "#eef0ec"
  muted-foreground: "#606a62"
  accent: "#e1ebe2"
  accent-foreground: "#173e2a"
  destructive: "#b32d37"
  border: "#dce1d9"
  input: "#c5cec3"
  ring: "#377c59"
  rail: "#162923"
  rail-foreground: "#e5efe7"
  dark-background: "#17201c"
  dark-foreground: "#e8eee9"
  dark-card: "#1e2923"
  dark-card-foreground: "#e8eee9"
  dark-popover: "#25322b"
  dark-popover-foreground: "#e8eee9"
  dark-primary: "#a2d1aa"
  dark-primary-foreground: "#163021"
  dark-secondary: "#293a2f"
  dark-secondary-foreground: "#d0e6d3"
  dark-muted: "#25312a"
  dark-muted-foreground: "#a7b6ab"
  dark-accent: "#304a39"
  dark-accent-foreground: "#ddf0df"
  dark-destructive: "#ff9b9b"
  dark-border: "#35433a"
  dark-input: "#53685a"
  dark-ring: "#95c9a5"
  dark-rail: "#111d17"
  dark-rail-foreground: "#e5efe7"
  navigation-text: "#b3c9bd"
  navigation-active: "#284638"
  status-good-fill: "oklch(50.8% 0.118 165.612)"
  status-good-text: "oklch(43.2% 0.095 166.913)"
  status-good-dark-text: "oklch(84.5% 0.143 164.978)"
  status-danger-fill: "oklch(57.7% 0.245 27.325)"
  status-danger-text: "oklch(50.5% 0.213 27.518)"
  status-danger-dark-text: "oklch(80.8% 0.114 19.571)"
  status-caution-fill: "oklch(55.5% 0.163 48.998)"
  status-caution-text: "oklch(47.3% 0.137 46.201)"
  status-caution-dark-text: "oklch(87.9% 0.169 91.605)"
typography:
  headline:
    fontFamily: "Inter Variable, sans-serif"
    fontSize: "1.5rem"
    fontWeight: 600
    lineHeight: "2rem"
    letterSpacing: "-0.025em"
  headline-wide:
    fontFamily: "Inter Variable, sans-serif"
    fontSize: "1.875rem"
    fontWeight: 600
    lineHeight: "2.25rem"
    letterSpacing: "-0.025em"
  body:
    fontFamily: "Inter Variable, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: "1.25rem"
  label:
    fontFamily: "Inter Variable, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 500
    lineHeight: 1
  metadata:
    fontFamily: "Inter Variable, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 400
    lineHeight: "1rem"
  code:
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, Liberation Mono, Courier New, monospace"
    fontSize: "0.75rem"
    fontWeight: 400
    lineHeight: 1.625
rounded:
  sm: "6px"
  md: "8px"
  lg: "10px"
  xl: "14px"
spacing:
  1: "4px"
  2: "8px"
  3: "12px"
  4: "16px"
  5: "20px"
  6: "24px"
  8: "32px"
  12: "48px"
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.primary-foreground}"
    rounded: "{rounded.md}"
    padding: "8px 16px"
    height: "36px"
  button-destructive:
    backgroundColor: "{colors.destructive}"
    textColor: "#ffffff"
    rounded: "{rounded.md}"
    padding: "8px 16px"
    height: "36px"
  button-outline:
    backgroundColor: "{colors.background}"
    textColor: "{colors.foreground}"
    rounded: "{rounded.md}"
    padding: "8px 16px"
    height: "36px"
  button-secondary:
    backgroundColor: "{colors.secondary}"
    textColor: "{colors.secondary-foreground}"
    rounded: "{rounded.md}"
    padding: "8px 16px"
    height: "36px"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.foreground}"
    rounded: "{rounded.md}"
    padding: "8px 16px"
    height: "36px"
  button-link:
    backgroundColor: "transparent"
    textColor: "{colors.primary}"
    rounded: "{rounded.md}"
    padding: "8px 16px"
    height: "36px"
  input:
    backgroundColor: "transparent"
    textColor: "{colors.foreground}"
    rounded: "{rounded.md}"
    padding: "4px 12px"
    height: "36px"
  native-select:
    backgroundColor: "{colors.card}"
    textColor: "{colors.foreground}"
    rounded: "{rounded.md}"
    padding: "0 12px"
    height: "36px"
  navigation:
    backgroundColor: "{colors.rail}"
    textColor: "{colors.navigation-text}"
    rounded: "6.4px"
    padding: "11.2px 12.8px"
  status-chip:
    backgroundColor: "transparent"
    textColor: "{colors.foreground}"
    rounded: "9999px"
    padding: "2px 8px"
  card:
    backgroundColor: "{colors.card}"
    textColor: "{colors.card-foreground}"
    rounded: "{rounded.lg}"
    padding: "20px"
---

# Design System: Code Mortis

## Overview

**Creative North Star: "Workflow-focused workspace"**

Code Mortis uses compact controls, clear review states, and a persistent forest-green navigation area. Warm off-white surfaces, restrained borders, and Inter Variable keep repository lists, review evidence, and settings readable.

This is a description of the implemented interface, not an approved creative metaphor or image-led brand direction. Mascot and distinctive branding work remain deferred. Light, dark, and system appearance share the same structure; system mode follows operating-system changes while the app is open.

**Key Characteristics:**

- Compact, native form controls and explicit action labels.
- Semantic status chips paired with text.
- Evidence and resolution history presented as separate, readable sections.
- Responsive navigation and restrained tonal surfaces.

## Colors

The palette combines forest-green actions and navigation with warm green-tinted neutrals. Frontmatter holds the actual light values and matching `dark-` values; the app switches the semantic CSS properties together.

### Primary

Primary green identifies consequential actions and links. The deeper rail anchors navigation in both appearances. The lighter dark-theme primary keeps those actions readable against dark surfaces.

### Neutral

Background is the workspace canvas; card and popover distinguish bounded content. Foreground carries reading text, muted foreground carries supporting information, and border/input separate surfaces and controls. Secondary and accent are related green-tinted interaction surfaces rather than additional brand accents.

Status colors retain their Tailwind OKLCH source values. Good states use emerald, failures and high severity use red, and partial states and medium severity use amber. Status fills use 8% opacity and borders 20%; dark mode changes the text tone. Other statuses remain neutral. Destructive actions and errors use the semantic destructive token.

## Typography

Inter Variable is the interface family, with sans-serif fallback. There is no separate display face. Platform monospace is reserved for code, paths, branches, and identifiers.

Page headlines use `headline`, changing to `headline-wide` at the small breakpoint. Standard content and buttons use body-sized type; section headings commonly use 16px semibold. Labels use medium weight and metadata uses the smaller role. Descriptive paragraphs use relaxed line height (1.625); Markdown review prose uses 1.7. Counts use tabular numerals. Do not add decorative uppercase labels.

## Layout

At 1024px and wider, the shell has a sticky 220px rail and a flexible main column. Content has a 1152px maximum width. Main padding is 20px horizontally and 32px vertically, increasing to 36px horizontal at 640px and 48px in both directions at 1024px. Below the desktop breakpoint, the rail becomes a top navigation area with horizontal links and a compact appearance select.

Lists group rows within one bordered surface. Typical row padding is 20px; filters and actions wrap with 8px or 12px gaps. At 640px, repository controls receive bounded widths, pull-request status moves to the row end, and connection rows become horizontal. Inputs use 16px text below 768px and 14px above it. Forms retain visible labels and hints.

Finding details occupy a right sheet, full width on small screens and capped at 672px from 640px. Repository configuration retains the sheet primitive's 75% width and a 576px cap from 640px. Sheets scroll vertically. Dialogs keep 16px outer margins; connection editing is capped at 576px and 90dvh. Setup uses a centered 512px column.

## Elevation & Depth

The workspace uses tonal surfaces and thin borders. Cards have no ambient shadow. Inputs, outline buttons, and switches use the small control shadow; active default tabs use a light shadow. Dialogs and sheets use the overlay shadow with a 50% black backdrop. Exact shadow values are in the sidecar, sourced from the installed Tailwind theme.

## Shapes

Controls use the medium radius, list containers the large radius, and status chips are fully rounded. The navigation uses a slightly tighter 6.4px radius. Borders are generally 1px. Evidence blocks use the medium radius and retain rectangular, scrollable code areas.

## Components

### Buttons

Default controls are 36px high with medium-weight 14px text and 8px/16px padding. Icon-bearing defaults reduce horizontal padding to 12px. Small buttons are 32px high; large buttons are 40px. Primary, outline, ghost, and destructive variants are used by the workspace. Secondary and link variants are available in the generated primitive.

Primary/destructive hover reduces fill opacity to 90%; outline and ghost use the accent surface. Secondary hover uses 80% opacity; links underline. Keyboard focus uses a 3px ring at 50% opacity. Disabled controls use 50% opacity and block interaction. The default state transition is 150ms; no hover lift is added.

### Inputs / Fields

Inputs and native selects are 36px high, with 12px horizontal padding and input-colored borders. Inputs are transparent in light mode and use a 30% input fill in dark mode; native selects use the card surface. Field hints are 12px muted text. Textareas grow with content with a 64px minimum height. Invalid fields use destructive borders and rings. Native controls retain browser affordances.

### Chips

Status chips use 12px medium text, 2px/8px padding, thin borders, and semantic tints. Text is capitalized and underscores become spaces. Queued/running states include a spinner only when motion is allowed. These chips communicate state and are not filter buttons.

### Cards / Containers

Content cards use the card surface, large radius, a 1px border, and usually 20px padding. Lists share a single container with divided rows; interactive rows use a 50% muted hover fill. Empty states use dashed borders and centered guidance.

### Navigation

Rail links combine a 16px outline icon with 13.12px text, a 11.2px gap, and 11.2px/12.8px padding. Active and hovered links share a brighter green fill and white text. The Repositories link remains active on pull and run detail routes. The compact `cm` mark is typographic, not a mascot. Appearance choices are explicitly System, Light, and Dark.

### Evidence and resolution history

Finding details separate Markdown explanation, original code, suggested change, actions, and Activity. Code blocks use a muted background, a 1px border, 16px padding, and 12px monospace text. The Activity list uses 2px left borders, 16px inner offsets, and 20px gaps; actor, source, time, and reason remain distinct. Pending GitHub confirmation and synchronization errors remain visible beside actions.

### Overlays and motion

Radix dialogs and sheets retain their accessible interaction behavior and close controls. Dialog transitions use 200ms; sheets open in 500ms and close in 300ms. Shared control transitions use the standard easing. Reduced-motion preference shortens animation and transition duration to 0.01ms and disables smooth scrolling. Do not add motion to static evidence.

## Do's and Don'ts

### Do:

- Do use semantic CSS properties so light, dark, and system appearance stay consistent.
- Do keep status wording visible alongside color, including partial coverage and publication failures.
- Do retain clear focus indicators, associated field labels, and reduced-motion behavior.
- Do wrap long prose and filenames appropriately while allowing code blocks to scroll horizontally.

### Don't:

- Don't add decorative eyebrow labels above page titles; retain functional repository and pull-request breadcrumbs.
- Don't replace native selects or the existing shadcn primitives without a concrete interaction need.
- Don't imply a finding is resolved merely because it is absent from a later review.
- Don't introduce a mascot, approved metaphor, or additional brand assets as if they were already established.
