---
name: "Code Mortis"
description: "A compact workspace for traceable code reviews."
colors:
  background: "#ffffff"
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
  destructive-foreground: "#ffffff"
  border: "#dce1d9"
  input: "#c5cec3"
  ring: "#377c59"
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
  dark-destructive-foreground: "#17201c"
  dark-border: "#35433a"
  dark-input: "#53685a"
  dark-ring: "#95c9a5"
  code-keyword: "#6639a6"
  code-string: "#245944"
  code-number: "#924808"
  code-function: "#125d8a"
  code-added: "#18472c"
  code-added-background: "#e5f1e7"
  code-removed: "#8c2530"
  code-removed-background: "#fbeaec"
  dark-code-keyword: "#d9b8ff"
  dark-code-string: "#a2d1aa"
  dark-code-number: "#efc28d"
  dark-code-function: "#9ad9f5"
  dark-code-added: "#b9e4c4"
  dark-code-added-background: "#243e2e"
  dark-code-removed: "#ffc1c7"
  dark-code-removed-background: "#4a292d"
  status-good-fill: "oklch(50.8% 0.118 165.612)"
  status-good-text: "oklch(43.2% 0.095 166.913)"
  status-good-dark-text: "oklch(84.5% 0.143 164.978)"
  status-danger-fill: "oklch(57.7% 0.245 27.325)"
  status-danger-text: "oklch(50.5% 0.213 27.518)"
  status-danger-dark-text: "oklch(80.8% 0.114 19.571)"
  status-caution-fill: "oklch(55.5% 0.163 48.998)"
  status-caution-text: "oklch(47.3% 0.137 46.201)"
  status-caution-dark-text: "oklch(87.9% 0.169 91.605)"
  status-running-fill: "oklch(48.8% 0.243 264.376)"
  status-running-text: "oklch(42.4% 0.199 265.638)"
  status-running-dark-text: "oklch(80.9% 0.105 251.813)"
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
  section:
    fontFamily: "Inter Variable, sans-serif"
    fontSize: "1.25rem"
    fontWeight: 600
    lineHeight: "1.75rem"
    letterSpacing: "-0.025em"
  finding-preview:
    fontFamily: "Inter Variable, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 500
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
    textColor: "{colors.destructive-foreground}"
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
    backgroundColor: "{colors.muted}"
    textColor: "{colors.foreground}"
    rounded: "{rounded.md}"
    padding: "8px"
    width: "256px"
  status-chip:
    backgroundColor: "transparent"
    textColor: "{colors.foreground}"
    rounded: "9999px"
    padding: "2px 8px"
  filter-chip:
    backgroundColor: "{colors.card}"
    textColor: "{colors.foreground}"
    rounded: "{rounded.md}"
    height: "36px"
  popover:
    backgroundColor: "{colors.popover}"
    textColor: "{colors.popover-foreground}"
    rounded: "{rounded.md}"
    padding: "8px"
    width: "288px"
  card:
    backgroundColor: "{colors.card}"
    textColor: "{colors.card-foreground}"
    rounded: "{rounded.lg}"
    padding: "20px"
  finding-card:
    backgroundColor: "{colors.card}"
    textColor: "{colors.card-foreground}"
    rounded: "0px"
    padding: "12px 16px"
    width: "100%"
  finding-sheet:
    backgroundColor: "{colors.background}"
    textColor: "{colors.foreground}"
    width: "100%"
  coverage-file-row:
    backgroundColor: "transparent"
    textColor: "{colors.foreground}"
    padding: "12px 16px"
    width: "100%"
---

# Design System: Code Mortis

## Overview

**Creative North Star: "Workflow-focused workspace"**

Code Mortis uses compact controls, clear review states, and a neutral sidebar that follows the workspace theme. The Code Mortis preset uses green accents and white light-mode surfaces; a fresh browser starts with the Claude Code preset in System color mode. Restrained borders and Inter Variable keep repository lists, review evidence, and settings readable across palettes.

This is a description of the implemented interface, not an approved creative metaphor or image-led brand direction. Mascot and distinctive branding work remain deferred. Every palette shares the same structure in Light, Dark, and System color modes. Appearance settings apply immediately and are saved in the current browser. System follows operating-system changes while the app is open; the header toggle saves a light or dark choice while preserving the selected palette.

**Key Characteristics:**

- Compact, native form controls and explicit action labels.
- Semantic status chips paired with text.
- Evidence and resolution history presented as separate, readable sections.
- Responsive navigation and restrained tonal surfaces.

This document records the current implementation. Semantic palette tokens come from the typed local registry in `frontend/src/lib/appearance.ts`; `frontend/src/index.css` maps those tokens to Tailwind and defines browser treatments. Reusable controls come from `frontend/src/components/ui/`. Appearance controls live in `frontend/src/components/appearance-settings.tsx`, with browser-local state in `frontend/src/main.tsx`. Shared finding and assessment rows live in `frontend/src/components/finding-card.tsx`; review sheets live in `frontend/src/reviews.tsx`, and security sheets live in `frontend/src/security.tsx`. Code presentation lives in `frontend/src/components/code.tsx`. Shared Previous/Next controls live in `frontend/src/components/pagination.tsx`, and review/scan stage summaries reuse `frontend/src/components/stage-card.tsx`. Analytics and MCP screens live in `frontend/src/analytics.tsx` and `frontend/src/mcp.tsx`. `.impeccable/design.json` supplies matching standalone component previews and extensions for the design panel.

## Colors

The Code Mortis preset combines forest-green actions and navigation accents with warm green-tinted neutrals. Frontmatter holds its actual light values and matching `dark-` values. The runtime registry also supplies Noodle, Aura, Carbonfox, Catppuccin, Claude Code, Cobalt2, Dracula, and Synthwave84, each with light and dark variants; the app switches the semantic CSS properties together.

Imported dark palettes are local snapshots of noodle-site's themes; their light variants are matching web adaptations. Imported foreground, action, and syntax colors are adjusted to a 4.5:1 contrast target against their used surfaces; focus-ring colors target 3:1. Preset metadata and the registry source pointer live in the sidecar rather than duplicating every variant here.

### Primary

Primary identifies consequential actions, links, the sidebar brand, and repository activity. The Code Mortis preset's green and lighter dark-mode green keep those elements readable against their surfaces; other presets use their own primary color for the same roles.

### Neutral

Background is the workspace canvas; card and popover distinguish bounded content. Foreground carries reading text, muted foreground carries supporting information, and border/input separate surfaces and controls. Secondary and accent are related interaction surfaces, green-tinted in the Code Mortis preset, rather than additional brand accents.

Sidebar colors reuse the workspace palette in every preset and mode: `--sidebar` aliases `--muted`, `--sidebar-foreground` aliases `--foreground`, and the sidebar primary, primary-foreground, accent, accent-foreground, border, and ring properties alias their matching semantic tokens. Selected and hovered links use the filled accent surface; repository owner labels and icons use muted foreground.

Status colors retain their Tailwind OKLCH source values. Good states use emerald, failures and high severity use red, and partial, blocked, outdated, and medium-severity states use amber. Running status chips use blue with their spinner; the sidebar's review activity spinner uses the selected primary color. Not reviewed, cancelled, superseded, and other statuses remain neutral. Status fills use 8% opacity and borders 20%; dark mode changes the text tone. Destructive actions and errors use the semantic destructive token; destructive buttons pair the full token background with `destructive-foreground` in both modes.

Code tokens separate keywords, strings, numbers, and functions in both themes; comments use muted foreground. Diff additions and removals pair their text token with a distinct tinted background and visible `+` or `−` markers. Syntax colors describe code, not finding severity or resolution.

Text selection uses accent and accent foreground. Text inputs and textareas use a primary-colored caret. Scrollbars are thin with an input-colored thumb and transparent track; link underlines use a 3px offset. The shared browser focus fallback is a 2px ring-colored outline with a 3px offset; component-specific focus treatments override it where defined.

## Typography

Inter Variable is the interface family, with sans-serif fallback. There is no separate display face. Platform monospace is reserved for code, paths, branches, and identifiers.

Inbox, repository, PR, and review-run headlines use `headline`, changing to `headline-wide` at the small breakpoint. Settings and repository-configuration titles use `headline-wide` at every width. Settings sections and the repository Pull requests heading use the 20px semibold `section` role. Other section headings commonly use 16px semibold, while list group and finding evidence headings use 14px semibold. Labels use medium weight and metadata uses the smaller role. Descriptive paragraphs use relaxed line height (1.625); Markdown review prose uses 1.7. Finding previews use 14px medium type, 20px line height, and a two-line clamp; full evidence remains available in the sheet. Counts use tabular numerals. Do not add decorative uppercase labels.

## Layout

At 1024px and wider, the shell has a fixed 256px sidebar and a flexible main column. The sidebar collapses completely offscreen and releases its full width to the main column. Below 1024px, navigation opens as a 288px left drawer with a backdrop. A sticky 56px header keeps the navigation toggle, page breadcrumbs, and far-right appearance toggle available at every width. Main content has a centered 1152px cap, 48px horizontal and 40px vertical padding on desktop; below 1024px it uses 20px horizontal and 32px vertical padding, increasing to 36px horizontal at 640px. Sidebar content scrolls independently between its brand header and footer.

Inbox rows share one bordered surface; repository PR groups use separate bordered surfaces with 24px between lists. Typical row padding is 20px; filters and actions wrap with 8px or 12px gaps. At 640px, repository controls receive bounded widths, pull-request status moves to the row end, and connection rows become horizontal. Inputs use 16px text below 768px and 14px above it. Forms retain visible labels and hints.

The inbox uses a wrapping toolbar adapted from Grudget’s subscriber filters: compact search, a Filters menu, active filter buttons, and ordering controls aligned to the right when space permits. Search fills the mobile width and uses 224px from 640px. Inbox uses an unframed header with a plain muted 14px matching count beside its title. The count retains a polite live announcement of matching PRs and repositories. Its 14px description follows after 12px; the toolbar and list each follow after 24px. At 768px and wider, a shared grid aligns pull-request details, review status, open findings, creation date, and a chevron. The first column flexes; the others use 160px, 104px, 96px, and 16px, with 16px gaps. Below 768px, column headings disappear, the title and repository metadata span the row, status and findings share the next line, and the date sits below. The chevron remains at the upper right. Titles wrap, repository and author identifiers can break, and selected filter summaries truncate within their controls. Rows use 16px padding on mobile and 20px horizontal/12px vertical padding from 768px.

Finding and assessment details occupy the same right sheet, full width on small screens and capped at 672px from 640px. The title, location, status or assessment-result strip, tabs, and action footer remain outside the independently scrolling reading area. The finding action footer can scroll independently up to 50dvh when expanded. Sections use 24px horizontal padding. Dialogs keep 16px outer margins; API connection editing is capped at 576px, personal connection editing at 512px, and both at 90dvh. Setup uses a centered 512px column.

Run-history rows use a grid: at 768px, a flexible reading column is followed by 104px and 160px state columns and a 16px chevron, with 16px gaps. Below 768px, headings and chevrons disappear, the reading column spans the row, and the two state fields sit beneath it. Rows use 20px horizontal and 16px vertical padding. Findings and assessments use full-width attached cards within a shared rounded border, with 1px separators and no gaps or individual frames. Each finding row uses 12px vertical padding, with 16px horizontal padding below 640px and 20px from 640px. Preview, metadata, and state stack on small screens; from 640px the state occupies a separate right-hand column. The shared filter bar search fills the mobile width and uses 224px from 640px; filter chips and review controls wrap.

Settings has a 176px section-navigation column from 1024px; repository configuration uses 180px navigation, a 40px gap, and a form column capped at 672px. Both navigation lists wrap horizontally below 1024px. Settings records adapt to their content container, not just the viewport: one column below 320px, two information columns from 320px, and aligned identity/model-or-access/status/actions columns with a muted heading row from 640px. Identity, notes, and actions span the narrow layouts. Team roles move beneath identity below a 400px container width; from 400px, identity, role, and actions align in one row. Long actions and badges wrap instead of forcing horizontal overflow.

Appearance orders its section heading and description, Color mode field, then Theme previews with 24px between sections. The mode field is capped at 320px. Theme previews use 12px gaps and respond to the settings container: one column below 440px, two from 440px, and three from 720px.

Review-run tabs use a full-width two-column grid below 640px and an inline row from 640px. PR tabs wrap, and security tabs use the same compact responsive treatment. Metadata in Details uses a 104px label column beside flexible, wrapping values at every width, with a 16px column gap.

## Elevation & Depth

The workspace uses tonal surfaces and thin borders. Cards have no ambient shadow. Inputs, outline buttons, and switches use the small control shadow; active default tabs use a light shadow. Filter popovers use the medium shadow and a 1px border-colored ring without a backdrop. Dialogs and sheets use the overlay shadow with a 50% black backdrop. Exact shadow values are in the sidecar, sourced from the installed Tailwind theme.

## Shapes

Controls and navigation links use the medium radius, list containers the large radius, and status chips are fully rounded. Borders are generally 1px. Evidence blocks use the medium radius and retain rectangular, scrollable code areas.

## Components

### Buttons

Default controls are 36px high with medium-weight 14px text and 8px/16px padding. Icon-bearing defaults reduce horizontal padding to 12px. Small buttons are 32px high; large buttons are 40px. Primary, outline, ghost, and destructive variants are used by the workspace. Secondary and link variants are available in the generated primitive.

Primary/destructive hover reduces fill opacity to 90%; outline and ghost use the accent surface. Secondary hover uses 80% opacity; links underline. Keyboard focus uses a 3px ring at 50% opacity. Disabled controls use 50% opacity and block interaction. The default state transition is 150ms; no hover lift is added.

The header appearance toggle is a 36px circular ghost button with a muted fill, primary-colored 18px sun/moon icons, and accent hover treatment. Its accessible label and title name the next action: Switch to light mode or Switch to dark mode. It changes the color mode while retaining the selected palette. Icons rotate and scale over 200ms with ease-out timing; reduced-motion preference disables these transitions.

### Tabs

Default tabs use the muted list surface and a subtle selected shadow. Light selection uses the workspace background and foreground. Dark selection uses muted foreground at 20% opacity for its fill and 40% for its border; labels use foreground and selected muted child counts use foreground at 90% opacity. The line variant stays transparent with its foreground underline. Both variants retain Radix keyboard selection and visible focus.

### Inputs / Fields

Inputs and native selects are 36px high, with 12px horizontal padding and input-colored borders. Inputs are transparent in light mode and use a 30% input fill in dark mode; native selects use the card surface. Field hints are 12px muted text. Textareas grow with content with a 64px minimum height. Invalid fields use destructive borders and rings. Native controls retain browser affordances.

Model, provider, API protocol, repository language and effort, repository/PR AI connection, and team-role selectors use searchable shadcn comboboxes installed with Bun. They share the card surface, 36px control height, and at least 40px option rows. Search matches labels and saved values; keyboard arrows and Enter select, while Escape closes the choices without closing the surrounding settings dialog. `SearchSelect` combines the Base UI Combobox with the existing Radix Popover for compatible focus and dismissal inside dialogs. Menus match the field width, use a 6px trigger offset and 16px collision padding, and scroll within a 240px option list. Labels wrap; empty choices explain that there are no matches. Models retain refresh, custom IDs, and connection defaults; language accepts suggestions or a custom language. Required and disabled behavior remains enforced. A selected provider icon is decorative and does not replace the field's accessible label.

### Chips

Status chips use 12px medium text, 2px/8px padding, thin borders, and semantic tints. Text is capitalized and underscores become spaces. Queued/running states include a spinner only when motion is allowed. These chips communicate state and are not filter buttons.

The Filters button uses the installed shadcn dropdown menu and lists properties without an active condition. Choosing a property opens its checkbox popover. Only active filters and the property currently being edited appear in the toolbar; Inbox keeps Open active implicitly without a redundant chip; selecting all states removes that condition. Dismissing a new filter without a selection leaves it in the menu. Active filter controls match the Filters button with a solid 1px outline, medium radius, and small shadow; dark mode uses the input border token. They retain the card surface, plus-circle icon, and property label. The inner hover edges where the filter and clear button meet are square. Selected values appear as compact secondary badges after a vertical separator: up to two labels on larger screens, a count for more selections, and a count on mobile. A separate sibling clear button removes the condition without nesting interactive controls. The popover also offers Clear filters or Include all states. Clearing removes the filter from the toolbar, makes it available in the menu again, and returns focus to Filters. Long selected values truncate; their full names remain available in the accessible label and option list.

Inbox search, selected filters, ordering, and loaded list pages are represented in the URL. Filters cover Repository, PR state, Review status, Author, and Open findings. Repository and Author options are searchable; checkbox choices support multiple values except Open findings, which accepts one. The default Inbox PR state selection is Open, and that sole implicit condition has no toolbar chip. The PR state filter changes that selection, and each state shows its matching count. Running reviews remain visible regardless of PR state, while respecting the other filters. Clearing that condition includes all states. The toolbar has no global Reset button; individual filter, search, and ordering clear actions remain available. The Inbox no-matches empty state offers View all pull requests, which clears other filters and explicitly includes all PR states. Search updates after 300ms, and browser navigation restores the URL's selections. A separate dropdown selects Created, Updated, Title, Repository, or Author; the adjacent arrow reverses direction and exposes its current meaning in a tooltip. The URL uses `ordering` with a leading minus for descending order, defaulting to `-created_at`. Dates initially sort descending and text ascending. Reset ordering returns to newest first. Ordering and filter changes reset all loaded page counts.

### Cards / Containers

Content cards use the card surface, large radius, a 1px border, and usually 20px padding. PR lists, run history, settings records, finding groups, and assessments share a single container with divided rows. Interactive rows and finding cards use a 50% muted hover fill. Empty states use dashed borders and centered guidance; settings group empty states pair their guidance with an available add/connect action. Errors use a destructive-tinted bordered alert with an icon and wrapping recovery text. Loading uses muted text and a reduced-motion-aware spinner.

### Finding cards

The shared finding row leads with the saved explanation in 14px medium text, clamped to two lines. Markdown markers are removed only from this preview. Severity and category follow as compact metadata alongside the path and line range. The path uses 12px monospace text, a muted directory, and a medium-weight foreground filename; it truncates with the full path available in its title. Below 640px the path moves above the other metadata, and the state moves below it. From 640px the state aligns in a separate right-hand column across both reading rows.

PR rows show the current resolution status, plus Sync pending or Sync needs attention when applicable. Review-run rows show New finding, Repeated finding, Recorded finding, or Resolved from that run's saved result. Assessment rows show the candidate decision, including Assessing while work runs. Resolution and assessment remain separate. The category badge uses Bug, Shield, Gauge, Wrench, or the fallback Tag icon while preserving the saved category label.

The whole row is one native button with no nested actions. Enter and Space open the details sheet. Rows use 12px vertical padding, 16px horizontal padding on mobile and 20px from 640px, a 6px row gap, a 50% muted hover fill, and a 2px inset focus ring. Adjacent rows share one rounded bordered container and 1px separators. There is no file icon before the preview, separate View details label, or trailing navigation chevron.

### Finding and assessment pagination

Paged lists use the shared Previous and Next controls, with a live muted 12px tabular label reading Page N of M · T total. Each request replaces the visible page. The footer stays inside the divided list container and uses 12px vertical padding, 16px horizontal padding on mobile and 20px from 640px, with wrapping controls and a 12px gap. It is hidden for a single page without an error. Boundary actions are disabled, in-flight navigation is unavailable, and recoverable errors keep retry controls beside the affected list.

Search, filters, and ordering apply to the full server result set before pagination; totals are authoritative server counts. Changes return the affected list to its first page. Saved run findings, assessments, security reports, scan history, and activity fetch their pages independently from page metadata. Access denial hides previously displayed records. Coverage already saved in a review report uses the same controls over a local slice of ten file rows.

### Navigation

The sidebar uses the shadcn Sidebar primitive installed through the CLI with Bun. Links combine 16px outline icons, 14px text, an 8px gap, and 8px padding. Inbox, Security, Analytics, and Settings rows are 40px high; repository rows are 48px. The Inbox link has a right-aligned 24px rounded-square counter with the sidebar background surface, thin sidebar border, and normal-weight 12px tabular numerals in both active and inactive states for open PRs across accessible repositories, independent of page filters. It uses the existing repository activity response, refreshes every five seconds, includes zero, and hides while loading or on request failure. Its accessible description identifies the count as open pull requests. Active links retain a filled accent background and accent foreground; hover uses the same treatment. Repository rows place the name above its owner in 12px muted text, truncate both within the available width, and expose the full `owner/name` through their accessible label and native title. Repository rows do not force hover tooltips in the expanded sidebar or mobile drawer. A repository with a running review shows only a green 12px spinner that animates only when motion is allowed; its accessible link label includes review running. Loading, empty, and retry states appear inside the repository group. Access-denied responses hide repository data and explain the permission failure without a transient-error retry action.

Inbox is the homepage at `/`; Security opens `/security` and Analytics opens `/analytics`. Security keeps a reduced-motion-aware activity spinner while scan work is queued, running, or blocked. Repository links open `/repositories/<id>`. Legacy `/repositories?repo=<id>` links redirect to that path, and legacy `configure` links redirect administrators to `/repositories/<id>/settings`, mapping `configure=limits` to `tab=limits` while retaining list filters. `/repositories` opens the first available repository. An unavailable ID never silently selects another repository. The repository page uses the full `owner/name` as its wrapping title, with selection handled in the sidebar. Administrators get a Manage repositories action beside the group label. Settings at `/settings` stays above the account row in the footer. The 48px account row uses the shadcn Avatar installed with Bun: a 32px rounded-square GitHub avatar, a two-letter initials fallback, the GitHub username, and a muted Administrator or Member label. The full row opens a 240px upward dropdown through a trailing three-dot affordance; it repeats account identity, links to AI connections, adds GitHub and Team for administrators, and separates Sign out beneath them. Menu actions are at least 40px tall below 1024px and 32px above it. During sign-out, the row is disabled and shows Signing out with a reduced-motion-aware spinner. The footer has no separating top border. The compact `cm` mark is typographic, not a mascot; its brand link returns to the unfiltered inbox.

The authenticated workspace header uses the shadcn Breadcrumb primitive installed through the CLI with Bun. Paths begin with Inbox, then the repository, Pull request #N, and Review run as applicable; settings show Inbox, Settings, and the active tab, while administrator repository settings end with Configure. Loading or failed lookups use generic page labels. Ancestor links use muted 14px text, chevron separators, and foreground hover; the current page uses medium-weight foreground text and exposes its current state. Below 640px, intermediate ancestors move into a shadcn dropdown opened by an ellipsis button, keeping Inbox and the current page visible. Long labels truncate with their full text available in titles; dropdown labels wrap.

Pull and run detail routes select the matching repository in the sidebar, including when reached from Inbox. Inbox is active only on the inbox page. The Inbox sidebar link and header breadcrumb restore the originating inbox URL, including its filters and loaded pages. When opened from a repository list, the matching repository sidebar link and breadcrumb restore that list's URL, filters, ordering, and loaded pages. Pull and run links preserve either return context. Choosing a sidebar link closes the mobile drawer. The persistent header navigation toggle exposes its expanded state and supports Command/Ctrl+B outside editing controls. The collapsed desktop panel is inert; collapsing it with the keyboard while focus is inside moves focus to the toggle. The mobile drawer retains sheet focus trapping and Escape dismissal, includes a visible Close navigation button, and restores focus to the header toggle when closed.

### PR inbox

Inbox combines every selected PR state into one bordered list with one column-heading row and a Previous/Next footer. It preserves the responsive row grid, repository/author metadata, review-status pills, unresolved-finding badges, creation date, and chevron. Open remains the default; Merged and Closed participate in the same list when selected. A sole Open condition remains active without a redundant toolbar chip. Explicitly including all states uses `state=all` and survives reload. Running reviews remain visible regardless of PR state, retain their actual PR state badge, and sort first, followed by queued open PRs and then other results.

Filters, search, ordering, and the selected page live in the URL. `all_pages` is the selected page number, not an accumulated page depth. Pages contain ten rows, with ordering applied before pagination and stable server totals. Creation date, newest first, is the default; text ordering is case-insensitive. Records without a creation date remain after dated records and show Pending sync. Background sync does not promote older PRs by changing this ordering. Filter and ordering changes reset the page; polling refreshes the selected page.

Whole rows link to PR details and preserve the Inbox return URL. Publication failed and Partially published stay separate from execution status and unresolved-finding counts. Positive unresolved counts use destructive text and a restrained tint; zero uses muted styling. Empty states distinguish an empty workspace from filters with no matches. View all pull requests clears the other filters and explicitly includes all states. Ignore and restore actions retain their confirmation, permission, and cancellation semantics.

### Repository pull requests

Repository headers remain unframed and transparent. The title wraps, with a muted normal-weight owner and semibold repository name. Metadata includes the security grade when available, visibility, and View on GitHub. Sync and administrator-only Configure actions align to the right and wrap below the title on narrow screens. The review-configuration strip follows after 24px with a single top divider and 16px top padding, separating Enabled or Paused from automatic/manual policy, file limits, and comment language.

The repository list reuses Inbox filters and PR rows with repository scope fixed by the page. Search, PR state, review status, author, open findings, ordering, and independent group pages live in the URL. Open is the implicit default without a redundant chip; `state=all` explicitly includes all states. Each nonempty group has a heading, server total, ten visible rows per page, and Previous/Next navigation. Running reviews come first and remain in Open regardless of actual PR state, retaining that state badge. Ordering offers Updated, Title, and Author, defaulting to Updated newest first. Branch, draft, fork, outdated-revision feedback, publication problems, and return navigation remain visible.

### Settings and repository configuration

Settings groups AI connections into Shared API connections and Your subscriptions, with ChatGPT and Claude Code accounts together in the latter. Records separate identity/provider, default model, account or test status, and permission-appropriate actions. Sign-in-required accounts expose Complete sign in in the status cell; action wording reflects account readiness. Settings navigation includes AI connections, MCP access, and Appearance; administrator navigation also adds GitHub and Team. GitHub installation records separate account identity, repository access, review readiness, and recovery actions. Active, suspended, and uninstalled states remain distinct; reconnect and refresh actions preserve saved history, with an empty Accounts to connect section explaining how to install on another account. Team records use 32px initials avatars, visible role badges, and a trailing action menu. Editing and removal dialogs restore focus to their triggering control.

Appearance is available to administrators and members at `/settings?tab=appearance`, in Settings navigation and the account menu, with an Appearance breadcrumb. Color mode uses the existing labeled native select with Light, Dark, and System choices. Theme previews are native radio inputs with associated labels, a bordered miniature workspace, the preset name, and a visible Selected checkmark. Checked previews use a primary border and 1px ring; keyboard focus uses a 2px ring-colored outline with a 4px offset. Previews show each preset in the resolved light or dark mode and apply on selection without a Save action. Mode uses the legacy `theme` storage key; palette uses `color-theme`. Invalid or unavailable storage falls back to System and Claude Code, and storage write failures leave the current selection usable for the session. Tokens apply before React renders and feed the shared sidebar, overlays, toaster, code, and diffs. Operational settings requests and their errors are disabled or hidden on this section.

Configure opens the administrator-only repository settings page at `/repositories/<id>/settings`. Four URL-selected sections are Review behavior, AI connection, Project context, and Limits & rules, defaulting to Review behavior. The form retains edits while switching sections. Review switches pair visible labels and hints with right-aligned controls in divided rows. Other fields become two columns from 640px where appropriate. A Save action stays after the form content with visible request and validation errors; success returns to the repository list and preserves its search, filters, ordering, and loaded pages. Invalid fields in another section reveal that section and receive focus.

Project context uses a resizable textarea with a 192px minimum and 384px maximum height. Generate context and Undo sit beside its label. Missing connection/model hints explain why generation is unavailable; progress and recovery text remain beside the field. Generated context stays editable and requires Save. A stale result never overwrites edits made while generation is in progress. Unlimited limit checkboxes disable the corresponding numeric field while preserving its previous finite value; personal subscriptions show their applicable context limit and fixed concurrency with an explanation.

### Pull request details

The PR header is unframed and transparent. Its wrapping title begins with the muted PR number; the metadata below contains state, author, draft when applicable, branch, saved revision, and GitHub link. A latest-review strip follows after 24px with a top divider and 16px top padding. Latest status, Inspect review, date, and Current commit or Earlier commit stay together. Connection selection and Run review are available for writable open PRs and remain disabled during active work, while ignored or paused. Read-only access and unavailable review reasons remain visible. Ignored PRs have a separate restore notice and confirmation dialog; restoration never resumes cancelled reviews.

Findings, Resolved, Dismissed, and Run history are independent URL-selected tabs. Findings contains only Open findings; the other resolution tabs preserve their own query, severity, type, ordering, and page selections. There is no redundant Status filter. Tab counts describe the complete saved state totals independently of active filters or loaded rows. Search uses the shared 300ms delay. Severity and searchable Type support multiple selections, including custom categories. Ordering defaults to severity, highest first. The server applies filters before returning ten rows per page, and Previous/Next replaces the visible page.

Each nonempty finding state has a compact heading, matching total, and joined finding-row container. Saved evidence remains accessible in the shared sheet, with current resolution and pending synchronization shown separately. Empty states distinguish no reviews, no recorded findings, no open findings, no resolved/dismissed findings, and filters with no matches. Where appropriate, View resolved findings changes tabs; Clear filters clears only the active scope. Missing from a later review never means resolved.

Run history fetches a server page when opened and uses the shared Previous/Next footer. Rows keep review status separate from GitHub publication and link to the saved run while preserving the originating Inbox or repository URL. Closing finding details restores focus to its row, falling back to search when the row is no longer mounted.

### Review run details

The review-run header remains unframed and transparent. Its title includes the execution Status chip, with permission-appropriate resume, cancel, and publication-retry actions. Date, trigger, revision, and Current commit or Earlier commit precede compact execution metrics. GitHub comments and GitHub check follow in a separate divided strip. Publication and check badges retain keyboard-accessible explanations, errors, and a GitHub check link when available. Legacy assessment and fix-verification problems remain explicit; execution completion never implies successful publication.

The shared stage card appears between header feedback and the tabs. PR summary, OCR, candidate assessment, and fix verification retain their separate saved statuses, usage, errors, and recovery actions in the stage-details popover. Waiting for verification is shown as waiting for finding history to settle. The card uses a 40% muted surface, 16px padding, a divided explanation row, and responsive title/progress columns. Its 2.8-second running sweep animates only while visible, the document is active, and reduced motion is not requested. Static evidence does not animate.

Findings, Resolved, Assessment, Coverage, Details, and Activity are independent URL-selected tabs; Findings is initially selected. Findings contains saved new, repeated, or unclassified occurrences from the run. Resolved contains the findings resolved by this run after verification and GitHub confirmation; later status changes do not rewrite that saved list. Both fetch server pages with search, severity, searchable Type, and ordering. Findings also offers Result. Tab totals come from saved run state counts. The matching/total toolbar count stays screen-reader-only.

Finding rows open At this run with immutable saved evidence. Current status and Activity describe the live finding separately; saved evidence remains readable when the live request fails. Candidate sheets retain Assessment and Evidence tabs and a link to their finding when present. Sheet identifiers and selected tabs remain in the URL and survive polling. Focus returns to the originating row, search, or appropriate page tab.

Details keeps saved connection, model, engine, account owner, initiator, timestamps, and revisions in unboxed sections with 104px muted label columns and wrapping values. Configuration used stays collapsed in a native disclosure and shows the immutable configuration snapshot. Activity shows saved operational events with explicit errors and technical details. Empty findings, absent coverage, or incomplete assessment never imply a clean review.

### Coverage

Coverage begins with muted 12px guidance and saved selected/completed/reused/failed/waived counts. Counts come from validated saved file results; zero scope and missing legacy scope stay explicit. Files not reviewed precedes Reviewed files. Each nonempty group uses the shared collapsed native disclosure: a rounded bordered card, 44px minimum summary, group name, complete saved count, and a chevron. Opening reveals compact divided file rows and independent Previous/Next navigation over local pages of ten records.

Rows use 12px vertical padding, 16px horizontal padding on mobile and 20px from 640px, a muted 16px file icon, wrapping 12px monospace paths, and outcome chips. Specific reasons appear inline; repetitive default explanations are omitted, and technical details use a disclosure. Inspect coverage report stays below the groups as a native disclosure with the saved JSON. Missing, recording, failed, waived, and unreviewed states remain distinct. Coverage never implies a clean assessment or absence of bugs.

### Candidate assessment

Assessment starts with muted 12px guidance and saved totals for validated findings, repeated observations, unsupported or withheld candidates, and published comments. Pending, incomplete, legacy, and error states remain explicit. The shared filter bar provides debounced search, Result, Severity, searchable Type, and ordering. Choice totals describe all candidates in the run. Filtering and ordering happen on the server before ten-row replacement pages; the selected page refreshes with current run revisions. Matching/total counts are announced without adding a visible toolbar counter.

Assessment rows reuse the compact finding component and distinguish Assessing, Awaiting assessment, New validated finding, Repeated observation, Not supported, Withheld, and assessment errors. Previous/Next and retry controls remain inside the joined list. Empty states distinguish no proposals from filters with no matches; View all assessments clears the active search and filters.

The details sheet keeps identity, severity/category, assessment outcome, and reviewed revision above Assessment and Evidence tabs. The scrolling body separates the outcome explanation, earlier history, changed conditions, cited Original or Current evidence, and the original proposal disclosure. Polling preserves the selected tab. Retry assessment remains conditional on permissions and run state, with waiting-for-verification and merged-PR limitations explained when applicable. The persistent footer offers Open finding or Close, and candidate decisions never directly change finding resolution.

### Evidence and resolution history

The Focused evidence sheet begins with Finding details, the wrapping full file path with a vertically centered file icon, and severity/category. Sheet headers omit line-number labels. A bordered, half-muted strip separates Current status from the evidence, with an 18px state icon, explicit Status chip, latest verification or preservation note, and View verification or View activity. That link selects Activity and moves focus to its tab. On mobile, the link sits beneath the status text with a 44px minimum target. PR sheets default to Evidence alongside Activity and its count; saved-run sheets add At this run and label live evidence Current status. The selected tab survives polling.

The reading area separates Explanation, code comparison, and a history note that every review keeps its original evidence, with 24px between sections. When original and suggested snippets differ and fit the comparison limit, Suggested change shows a unified syntax-colored diff, addition/removal counts, and a filename header labeled Original → Suggested. Missing, equal, or oversized snippets use labeled code blocks as available. Code regions retain full source text, 12px monospace type, horizontal scrolling, and keyboard focus; ordinary snippets use the muted surface, a 1px border, and 16px padding, while a combined diff uses the workspace background and 12px padding inside one bordered container. Syntax highlighting derives from the file language and falls back to plain text for unknown languages or snippets above 60,000 characters; diff generation uses the same combined-size limit.

Activity shows fix verifications before the event list. Verification status, reviewed commit, explanation, optional original/current evidence disclosures, automatic-resolution result, and errors remain distinct. Events use a 1px vertical border, 7px timeline dots, 20px text offsets, and 20px gaps. Actor, source, and time use muted 12px text; reasons expand under View reason. Empty, loading, and unavailable states name the missing information.

The bordered footer keeps the GitHub thread link, Resolve finding or Reopen finding, and permission-appropriate Dismiss or Keep finding open actions outside the evidence scroll. Pending confirmation retains the confirmed current state, explains the requested change, and disables the main mutation until confirmation; failed synchronization exposes Retry sync and its error. On mobile, resolution actions precede the full-width GitHub link, with minimum 44px targets; from 640px they align horizontally with 36px targets. Dismissal expands an autofocus reason field and Confirm dismissal in the footer, requires a nonempty reason, and limits it to 2,000 characters. Read-only users retain evidence and history without mutation controls.

### Security workspace

Security reuses the application shell, unframed headers, semantic status/grade badges, shared filter bar, compact finding rows, sheets, stage card, coverage disclosures, and Previous/Next footer. The overview lists repositories with security grade, scan status, open findings, and latest scan. Search, Grade, Scan status, and ordering apply before server pagination. A grade remains separate from coverage and freshness; an ungraded or incomplete scan is never shown as clean.

Repository pages keep independent Findings, Resolved, False positives, Accepted risk, and Scan history tabs. Scan pages keep the same resolution tabs plus Assessment, Coverage, and Activity. The default Findings view contains Open only, without a redundant Status filter. Search, severity, type, result where supported, ordering, and page state remain scoped to their view. Counts come from persisted repository or scan totals rather than loaded pages. Report findings fetch immutable evidence from the paginated saved-report endpoint, and incomplete analysis and unsettled candidates remain visible.

Finding sheets separate At this scan from current resolution and activity. Scanned evidence includes explanation, trigger, impact, citations with file/line context, remediation, and verification. Current finding actions preserve permissions, version checks, resolution reasons, and pending GitHub issue synchronization. Issue creation opens an exact preview/edit dialog before publishing; bulk creation selects eligible findings before reviewing their individual drafts. Scan setup, retry, cancel, rescan, and report downloads retain their existing availability rules and visible errors.

### Analytics

Analytics uses an unframed heading, shared repository/filter/order controls, searchable period selection, custom-date calendar popovers, and a Refresh action. Overview, Findings, Reviews, and Usage are URL-selected tabs. Metrics use tabular numerals and divided, unboxed groups; charts and breakdown tables reuse semantic theme and severity colors. Tooltips, labels, and accessible tables retain the data without relying on color alone.

Loading, invalid dates, unavailable data, and empty history are distinct. About these metrics explains the date scope, confirmed resolution, validated coverage, reported tokens, missing reports, and denominators. Unknown or missing usage is never rendered as zero consumption or a billing estimate. Drill-down controls preserve the selected repositories and period.

### MCP settings and consent

MCP access uses the existing Settings navigation and section rhythm. Administrators control separate Reviews, Security, and management switches, allowed metadata hosts, and registered clients. Members see instance availability and their own revocable grants. Connection instructions, client records, and grant scopes use wrapping content, explicit labels, and permission-appropriate actions.

Agent authorization and exact-action approval are separate browser pages capped at 672px. Consent identifies the client, callback, domain, expiry, repositories, and offered permissions; read scopes start selected, and approving requires at least one repository and permission. Action approval shows the saved summary and exact arguments before explicit Approve or Deny actions. Expired, denied, already decided, failed, and loading states remain explicit. These pages do not expose credential entry or imply that signing in approved an agent.

### Overlays and motion

Radix dialogs and sheets retain their accessible interaction behavior and close controls. Dialog transitions use 200ms; sheets open in 500ms and close in 300ms. Desktop sidebar position and main-column gap transitions use 200ms linear easing. Shared control transitions use the standard easing. Reduced-motion preference shortens animation and transition duration to 0.01ms and disables smooth scrolling. Do not add motion to static evidence.

Popover, Checkbox, and DropdownMenu are added through the shadcn CLI using Bun. Their generated primitives wrap Radix; inbox-specific sizing and positioning are supplied through props. Filter popovers render in a portal, align to the trigger start with an 8px offset, keep 16px viewport collision padding, and cap their width at the viewport minus 32px. Filter choices scroll within a 256px maximum height. Opening and closing use fade and scale animations, with directional movement on opening. Keyboard activation, Escape dismissal, outside interaction, and focus return retain Radix behavior; clearing a condition or dismissing an unused filter returns focus to Filters. Closing an active filter returns focus to its property button. Ordering uses radio menu items with arrow-key navigation, checked state, Escape dismissal, and focus return. Checkbox rows are at least 40px tall and expose focus around the full label. The final selected PR state cannot be unchecked, keeping the selection meaningful.

## Do's and Don'ts

### Do:

- Do use the shared appearance registry and semantic CSS properties so every palette stays consistent in Light, Dark, and System modes.
- Do keep status wording visible alongside color, including partial coverage and publication failures.
- Do retain clear focus indicators, associated field labels, and reduced-motion behavior.
- Do wrap long prose and filenames appropriately while allowing code blocks to scroll horizontally.
- Do keep finding previews to two lines while preserving the full explanation in the details sheet.
- Do distinguish saved review evidence, current resolution, not-observed history, and pending GitHub synchronization.
- Do keep finding tabs and resolution actions outside the scrolling evidence area, and restore focus to the card when the sheet closes.
- Do reuse the Focused card and right-sheet structure for assessments, keeping assessment outcomes separate from finding resolution.
- Do keep Previous/Next pagination inside its list container, using server totals and replacing the visible page.

### Don't:

- Don't add decorative eyebrow labels above page titles; retain functional repository and pull-request breadcrumbs.
- Don't replace native selects or the existing shadcn primitives without a concrete interaction need.
- Don't imply a finding is resolved merely because it is absent from a later review.
- Don't wrap repository, pull-request, or review-run page headers in cards.
- Don't replace the compact partial-assessment badge with a separate warning banner.
- Don't introduce a mascot, approved metaphor, or additional brand assets as if they were already established.
