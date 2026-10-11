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
    lineHeight: 1.625
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
    rounded: "{rounded.lg}"
    padding: "16px"
    width: "100%"
  finding-sheet:
    backgroundColor: "{colors.background}"
    textColor: "{colors.foreground}"
    width: "100%"
  coverage-file-row:
    backgroundColor: "transparent"
    textColor: "{colors.foreground}"
    padding: "12px 0"
    width: "100%"
---

# Design System: Code Mortis

## Overview

**Creative North Star: "Workflow-focused workspace"**

Code Mortis uses compact controls, clear review states, and a neutral sidebar with green accents that follows the workspace theme. White light-mode surfaces, restrained borders, and Inter Variable keep repository lists, review evidence, and settings readable.

This is a description of the implemented interface, not an approved creative metaphor or image-led brand direction. Mascot and distinctive branding work remain deferred. Light and dark appearance share the same structure. Until a manual choice is saved, appearance follows operating-system changes while the app is open; the header toggle saves a persistent light or dark choice.

**Key Characteristics:**

- Compact, native form controls and explicit action labels.
- Semantic status chips paired with text.
- Evidence and resolution history presented as separate, readable sections.
- Responsive navigation and restrained tonal surfaces.

This document records the current implementation. Semantic colors and browser treatments come from `frontend/src/index.css`; reusable controls come from `frontend/src/components/ui/`. Finding and assessment cards and sheets live in `frontend/src/reviews.tsx`, with code presentation in `frontend/src/components/code.tsx`. `.impeccable/design.json` supplies matching standalone component previews and extensions for the design panel.

## Colors

The palette combines forest-green actions and navigation accents with warm green-tinted neutrals. Frontmatter holds the actual light values and matching `dark-` values; the app switches the semantic CSS properties together.

### Primary

Primary green identifies consequential actions, links, the sidebar brand, and repository activity. The lighter dark-theme primary keeps those elements readable against dark surfaces.

### Neutral

Background is the workspace canvas; card and popover distinguish bounded content. Foreground carries reading text, muted foreground carries supporting information, and border/input separate surfaces and controls. Secondary and accent are related green-tinted interaction surfaces rather than additional brand accents.

Sidebar colors reuse the workspace palette in both themes: `--sidebar` aliases `--muted`, `--sidebar-foreground` aliases `--foreground`, and the sidebar primary, primary-foreground, accent, accent-foreground, border, and ring properties alias their matching semantic tokens. Selected and hovered links use the filled accent surface; repository owner labels and icons use muted foreground.

Status colors retain their Tailwind OKLCH source values. Good states use emerald, failures and high severity use red, and partial, blocked, outdated, and medium-severity states use amber. Running status chips use blue with their spinner; the sidebar's review activity spinner uses primary green. Not reviewed, cancelled, superseded, and other statuses remain neutral. Status fills use 8% opacity and borders 20%; dark mode changes the text tone. Destructive actions and errors use the semantic destructive token.

Code tokens separate keywords, strings, numbers, and functions in both themes; comments use muted foreground. Diff additions and removals pair their text token with a distinct tinted background and visible `+` or `−` markers. Syntax colors describe code, not finding severity or resolution.

Text selection uses accent and accent foreground. Text inputs and textareas use a primary-colored caret. Scrollbars are thin with an input-colored thumb and transparent track; link underlines use a 3px offset. The shared browser focus fallback is a 2px ring-colored outline with a 3px offset; component-specific focus treatments override it where defined.

## Typography

Inter Variable is the interface family, with sans-serif fallback. There is no separate display face. Platform monospace is reserved for code, paths, branches, and identifiers.

PR and review-run headlines use `headline`, changing to `headline-wide` at the small breakpoint. Inbox, repository, Settings, and repository-configuration titles use `headline-wide` at every width. Settings sections and the repository Pull requests heading use the 20px semibold `section` role. Other section headings commonly use 16px semibold, while list group and finding evidence headings use 14px semibold. Labels use medium weight and metadata uses the smaller role. Descriptive paragraphs use relaxed line height (1.625); Markdown review prose uses 1.7. Finding previews use 14px medium type, relaxed line height, a 75ch maximum measure, and a three-line clamp; full evidence remains available in the sheet. Counts use tabular numerals. Do not add decorative uppercase labels.

## Layout

At 1024px and wider, the shell has a fixed 256px sidebar and a flexible main column. The sidebar collapses completely offscreen and releases its full width to the main column. Below 1024px, navigation opens as a 288px left drawer with a backdrop. A sticky 56px header keeps the navigation toggle, page breadcrumbs, and far-right appearance toggle available at every width. Main content has a centered 1152px cap, 48px horizontal and 40px vertical padding on desktop; below 1024px it uses 20px horizontal and 32px vertical padding, increasing to 36px horizontal at 640px. Sidebar content scrolls independently between its brand header and footer.

Each inbox list groups rows within its own bordered surface, with 24px between lists. Typical row padding is 20px; filters and actions wrap with 8px or 12px gaps. At 640px, repository controls receive bounded widths, pull-request status moves to the row end, and connection rows become horizontal. Inputs use 16px text below 768px and 14px above it. Forms retain visible labels and hints.

The inbox uses a wrapping toolbar adapted from Grudget’s subscriber filters: compact search, a Filters menu, active filter buttons, and ordering controls aligned to the right when space permits. Search fills the mobile width and uses 224px from 640px. The result count sits below. At 768px and wider, a shared grid aligns pull-request details, review status, open findings, creation date, and a chevron. The first column flexes; the others use 160px, 104px, 96px, and 16px, with 16px gaps. Below 768px, column headings disappear, the title and repository metadata span the row, status and findings share the next line, and the date sits below. The chevron remains at the upper right. Titles wrap, repository and author identifiers can break, and selected filter summaries truncate within their controls. Rows use 16px padding on mobile and 20px horizontal/12px vertical padding from 768px.

Finding and assessment details occupy the same right sheet, full width on small screens and capped at 672px from 640px. The title, location, status or assessment-result strip, tabs, and action footer remain outside the independently scrolling reading area. The finding action footer can scroll independently up to 50dvh when expanded. Sections use 24px horizontal padding. Dialogs keep 16px outer margins; API connection editing is capped at 576px, personal connection editing at 512px, and both at 90dvh. Setup uses a centered 512px column.

Run-history rows use a grid: at 768px, a flexible reading column is followed by 104px and 160px state columns and a 16px chevron, with 16px gaps. Below 768px, headings and chevrons disappear, the reading column spans the row, and the two state fields sit beneath it. Rows use 20px horizontal and 16px vertical padding. Findings and assessments use individual full-width cards with 12px gaps, 16px padding below 640px, and 20px from 640px. Card location and state stack on small screens and align across the header from 640px. The shared filter bar search fills the mobile width and uses 224px from 640px; filter chips and review controls wrap.

Settings has a 176px section-navigation column from 1024px; repository configuration uses 180px navigation, a 40px gap, and a form column capped at 672px. Both navigation lists wrap horizontally below 1024px. Settings records adapt to their content container, not just the viewport: one column below 320px, two information columns from 320px, and aligned identity/model-or-access/status/actions columns with a muted heading row from 640px. Identity, notes, and actions span the narrow layouts. Team roles move beneath identity below a 400px container width; from 400px, identity, role, and actions align in one row. Long actions and badges wrap instead of forcing horizontal overflow.

Review-run tabs use a full-width two-by-two grid below 640px and an inline row from 640px. Metadata in Details uses stacked labels and values on small screens, changing to a 160px label column beside flexible values from 640px.

## Elevation & Depth

The workspace uses tonal surfaces and thin borders. Cards have no ambient shadow. Inputs, outline buttons, and switches use the small control shadow; active default tabs use a light shadow. Filter popovers use the medium shadow and a 1px border-colored ring without a backdrop. Dialogs and sheets use the overlay shadow with a 50% black backdrop. Exact shadow values are in the sidecar, sourced from the installed Tailwind theme.

## Shapes

Controls and navigation links use the medium radius, list containers the large radius, and status chips are fully rounded. Borders are generally 1px. Evidence blocks use the medium radius and retain rectangular, scrollable code areas.

## Components

### Buttons

Default controls are 36px high with medium-weight 14px text and 8px/16px padding. Icon-bearing defaults reduce horizontal padding to 12px. Small buttons are 32px high; large buttons are 40px. Primary, outline, ghost, and destructive variants are used by the workspace. Secondary and link variants are available in the generated primitive.

Primary/destructive hover reduces fill opacity to 90%; outline and ghost use the accent surface. Secondary hover uses 80% opacity; links underline. Keyboard focus uses a 3px ring at 50% opacity. Disabled controls use 50% opacity and block interaction. The default state transition is 150ms; no hover lift is added.

The header appearance toggle is a 36px circular ghost button with a muted fill, primary-colored 18px sun/moon icons, and accent hover treatment. Its accessible label and title name the next action: Switch to light mode or Switch to dark mode. Icons rotate and scale over 200ms with ease-out timing; reduced-motion preference disables these transitions.

### Inputs / Fields

Inputs and native selects are 36px high, with 12px horizontal padding and input-colored borders. Inputs are transparent in light mode and use a 30% input fill in dark mode; native selects use the card surface. Field hints are 12px muted text. Textareas grow with content with a 64px minimum height. Invalid fields use destructive borders and rings. Native controls retain browser affordances.

Model, provider, API protocol, repository language and effort, repository/PR AI connection, and team-role selectors use searchable shadcn comboboxes installed with Bun. They share the card surface, 36px control height, and at least 40px option rows. Search matches labels and saved values; keyboard arrows and Enter select, while Escape closes the choices without closing the surrounding settings dialog. `SearchSelect` combines the Base UI Combobox with the existing Radix Popover for compatible focus and dismissal inside dialogs. Menus match the field width, use a 6px trigger offset and 16px collision padding, and scroll within a 240px option list. Labels wrap; empty choices explain that there are no matches. Models retain refresh, custom IDs, and connection defaults; language accepts suggestions or a custom language. Required and disabled behavior remains enforced. A selected provider icon is decorative and does not replace the field's accessible label.

### Chips

Status chips use 12px medium text, 2px/8px padding, thin borders, and semantic tints. Text is capitalized and underscores become spaces. Queued/running states include a spinner only when motion is allowed. These chips communicate state and are not filter buttons.

The Filters button uses the installed shadcn dropdown menu and lists properties without an active condition. Choosing a property opens its checkbox popover. Only active filters and the property currently being edited appear in the toolbar; Inbox starts with an active Open PR state condition; selecting all states removes that condition. Dismissing a new filter without a selection leaves it in the menu. Active filter controls match the Filters button with a solid 1px outline, medium radius, and small shadow; dark mode uses the input border token. They retain the card surface, plus-circle icon, and property label. The inner hover edges where the filter and clear button meet are square. Selected values appear as compact secondary badges after a vertical separator: up to two labels on larger screens, a count for more selections, and a count on mobile. A separate sibling clear button removes the condition without nesting interactive controls. The popover also offers Clear filters or Include all states. Clearing removes the filter from the toolbar, makes it available in the menu again, and returns focus to Filters. Long selected values truncate; their full names remain available in the accessible label and option list.

Inbox search, selected filters, ordering, and loaded group pages are represented in the URL. Filters cover Repository, PR state, Review status, Author, and Open findings. Repository and Author options are searchable; checkbox choices support multiple values except Open findings, which accepts one. The default Inbox PR state selection is Open. The PR state filter changes that selection, and each state shows its matching count. Running reviews remain visible regardless of PR state, while respecting the other filters. Clearing that condition includes all states. The toolbar has no global Reset button; individual filter, search, and ordering clear actions remain available. The Inbox no-matches empty state offers View all pull requests, which clears other filters and explicitly includes all PR states. Search updates after 300ms, and browser navigation restores the URL's selections. A separate dropdown selects Created, Updated, Title, Repository, or Author; the adjacent arrow reverses direction and exposes its current meaning in a tooltip. The URL uses `ordering` with a leading minus for descending order, defaulting to `-created_at`. Dates initially sort descending and text ascending. Reset ordering returns to newest first. Ordering and filter changes reset all loaded page counts.

### Cards / Containers

Content cards use the card surface, large radius, a 1px border, and usually 20px padding. PR lists, run history, and settings records share a single container with divided rows; findings and assessments use separate cards. Interactive rows and finding cards use a 50% muted hover fill. Empty states use dashed borders and centered guidance; settings group empty states pair their guidance with an available add/connect action. Errors use a destructive-tinted bordered alert with an icon and wrapping recovery text. Loading uses muted text and a reduced-motion-aware spinner.

### Finding cards

The shared Focused card begins with a 16px file icon, a medium-weight 12px monospace filename and line range, and the full directory on a separate muted line. Locations wrap, including long unbroken names. PR cards put the current Status chip and any Sync pending or Sync needs attention note in the opposite header position; resolved chips include a check icon. Review-run cards put Inline comment or Summary comment there instead, preserving the saved occurrence rather than suggesting a current resolution.

The explanation follows after 16px, using the `finding-preview` role. Markdown markers are removed from this preview only. Another 16px separates the footer, with severity and category on the left and View details plus a chevron on the right. A historical note follows after 12px with a small history icon. Not observed in latest review remains separate from the current Status chip. The whole card is one native button, with no nested actions; Enter and Space open the sheet. Keyboard focus uses a 2px inset ring, and hover makes the details affordance primary-colored without moving the card.

Assessment cards reuse this component with the proposed finding's saved location, explanation, severity, and category. Their header chip reads New validated finding, Repeated observation, Not supported, Withheld, or Awaiting assessment; the footer action reads View assessment. Assessment needs attention and Linked to a saved finding appear as separate notes. The result chip describes the candidate's assessment decision; finding resolution remains separate.

### Finding and assessment pagination

PR finding groups and the assessment list share a full-width 1px top separator in the semantic border color, 16px after the last card and 12px before the controls. The footer has no horizontal inset or extra bottom padding: the small ghost Load more button aligns with the cards' left edge and the muted 12px tabular shown/total count aligns with their right edge. Controls wrap with a 12px gap when needed. Loading disables Load more and changes its label to Loading…. Pagination visibility and recovery behavior follow each list's rules below.

### Navigation

The sidebar uses the shadcn Sidebar primitive installed through the CLI with Bun. Links combine 16px outline icons, 14px text, an 8px gap, and 8px padding. Inbox and Settings rows are 40px high; repository rows are 48px. The Inbox link has a right-aligned 24px rounded-square counter with the sidebar background surface, thin sidebar border, and normal-weight 12px tabular numerals in both active and inactive states for open PRs across accessible repositories, independent of page filters. It uses the existing repository activity response, refreshes every five seconds, includes zero, and hides while loading or on request failure. Its accessible description identifies the count as open pull requests. Active links retain a filled accent background and accent foreground; hover uses the same treatment. Repository rows place the name above its owner in 12px muted text, truncate both within the available width, and expose the full `owner/name` through their accessible label and native title. Repository rows do not force hover tooltips in the expanded sidebar or mobile drawer. A repository with a running review shows only a green 12px spinner that animates only when motion is allowed; its accessible link label includes review running. Loading, empty, and retry states appear inside the repository group. Access-denied responses hide repository data and explain the permission failure without a transient-error retry action.

Inbox is the homepage at `/`; repository links open `/repositories/<id>`. Legacy `/repositories?repo=<id>` links redirect to that path, and legacy `configure` links redirect administrators to `/repositories/<id>/settings`, mapping `configure=limits` to `tab=limits` while retaining list filters. `/repositories` opens the first available repository. An unavailable ID never silently selects another repository. The repository page uses the full `owner/name` as its wrapping title, with selection handled in the sidebar. Administrators get a Manage repositories action beside the group label. Settings at `/settings` stays above the account row in the footer. The 48px account row uses the shadcn Avatar installed with Bun: a 32px rounded-square GitHub avatar, a two-letter initials fallback, the GitHub username, and a muted Administrator or Member label. The full row opens a 240px upward dropdown through a trailing three-dot affordance; it repeats account identity, links to AI connections, adds GitHub and Team for administrators, and separates Sign out beneath them. Menu actions are at least 40px tall below 1024px and 32px above it. During sign-out, the row is disabled and shows Signing out with a reduced-motion-aware spinner. The footer has no separating top border. The compact `cm` mark is typographic, not a mascot; its brand link returns to the unfiltered inbox.

The authenticated workspace header uses the shadcn Breadcrumb primitive installed through the CLI with Bun. Paths begin with Inbox, then the repository, Pull request #N, and Review run as applicable; settings show Inbox, Settings, and the active tab, while administrator repository settings end with Configure. Loading or failed lookups use generic page labels. Ancestor links use muted 14px text, chevron separators, and foreground hover; the current page uses medium-weight foreground text and exposes its current state. Below 640px, intermediate ancestors move into a shadcn dropdown opened by an ellipsis button, keeping Inbox and the current page visible. Long labels truncate with their full text available in titles; dropdown labels wrap.

Pull and run detail routes select the matching repository in the sidebar, including when reached from Inbox. Inbox is active only on the inbox page. The Inbox sidebar link and header breadcrumb restore the originating inbox URL, including its filters and loaded pages. When opened from a repository list, the matching repository sidebar link and breadcrumb restore that list's URL, filters, ordering, and loaded pages. Pull and run links preserve either return context. Choosing a sidebar link closes the mobile drawer. The persistent header navigation toggle exposes its expanded state and supports Command/Ctrl+B outside editing controls. The collapsed desktop panel is inert; collapsing it with the keyboard while focus is inside moves focus to the toggle. The mobile drawer retains sheet focus trapping and Escape dismissal, includes a visible Close navigation button, and restores focus to the header toggle when closed.

### Grouped PR inbox

Separate bordered lists appear in order: Open, Merged, and Closed. The default Inbox view includes open PRs, including reviewed open PRs. Merged and Closed appear when selected. Only nonempty lists appear. A single visible list has no section heading; multiple lists have static 14px semibold labels and smaller muted total counts above their bordered surfaces, with 10px between each label and its list. Section labels have no filled header bar or collapse control. Lists retain accessible region names even without visible headings. Each included state has its nonempty list with column labels and an independent Load more action. A PR with any running review stays in Open until that run finishes, even if the PR has merged or closed; it never appears in two lists. Those rows display their actual PR state. If the running run is on an older revision, the current-revision review status remains accurate and a separate note explains that a review is still running. Completed reviews of open PRs remain in the same list. Active work places running reviews first, then queued reviews, then the remaining statuses; rows within each priority default to GitHub creation date, newest first, with a stable ID tie-breaker. The selected ordering applies within each priority and group, before pagination; text ordering is case-insensitive. Edits and background syncs do not promote older PRs. Existing records without a creation date appear last within their priority and show Pending sync until synchronization fills the date. Each group initially shows up to 10 PRs, and Load more adds another 10. Groups load more independently and keep pagination failures and retry actions beside the affected group.

Inbox, repository PRs, PR findings, and candidate assessments fetch pages of 10 from the server. Search, filtering, and available ordering apply to the full result set before pagination; displayed totals describe all matches, not only loaded rows. Duplicate rows between page requests appear once without reducing the server totals. First-page lists refresh every five seconds; requesting additional pages pauses that list's automatic refresh. Grouped lists load independently. Recoverable page failures keep successfully fetched rows available and place retry actions beside the affected group or list. Access denial hides previously loaded rows. After 10 pages in a group or list, remaining matches prompt the user to narrow the filters.

Whole rows link to PR details and carry the inbox URL for the return path. All PR rows use the same card background, including running reviews; hover uses the shared muted fill. Review status, publication failures, and open finding counts remain distinct. Publication failed and Partially published appear immediately after the username in row metadata, using destructive and amber warning text respectively. Finding badges read “N unresolved” so they cannot be mistaken for the PR state. Positive finding counts use destructive text and a restrained tint; zero uses muted styling. Reviewed open PRs can still contain open findings. Empty results distinguish an empty workspace from filters with no matches. Clearing the PR state condition explicitly includes all states and survives reload; opening Inbox without state parameters selects Open. The combined list retains `active_pages` in the URL; legacy `reviewed_pages` is accepted when `active_pages` is absent.

### Repository pull requests

The repository header pairs its wrapping name with visibility and a GitHub link, plus Sync and an administrator-only Configure action. A compact strip with top and bottom borders shows whether reviews are enabled or paused. Enabled repositories show automatic or manual review policy, the file limit, and comment language; paused repositories show permission-appropriate guidance. The strip wraps on mobile and precedes the Pull requests heading and shared Inbox toolbar.

Repository lists reuse Inbox's filters, responsive row grid, status chips, and unresolved-finding badges. Repository scope is fixed by the page, so the toolbar offers PR state, Review status, Author, and Open findings filters. Search, filters, ordering, and loaded group pages live in the URL. Ordering offers Updated, Title, and Author, defaulting to Updated newest first; the date column is Updated. Running reviews come first, then current-revision queued reviews, with the selected order applied within each priority before pagination. Row metadata retains the PR number, author, branch, draft and fork labels, and actionable review feedback. Reviews on an older revision show Outdated, with a separate note if that review is still running.

Repository lists default to Open, including when a missing or invalid state is normalized. Merged and Closed appear when selected; explicitly including all states is represented by `state=all` and survives reload and navigation. Each nonempty group keeps its heading and total count, starts with up to 10 rows, and has an independent Load more action fetching another page of 10. Running reviews remain in Open regardless of PR state and retain their actual state badge. Each group displays at most 100 rows before asking the user to narrow the filters. The server applies repository scope, filters, and ordering before returning each page. Filter and ordering changes reset the loaded pages, while row links preserve the repository return context.

### Settings and repository configuration

Settings groups AI connections into Shared API connections and Your subscriptions, with ChatGPT and Claude Code accounts together in the latter. Records separate identity/provider, default model, account or test status, and permission-appropriate actions. Sign-in-required accounts expose Complete sign in in the status cell; action wording reflects account readiness. Administrator navigation adds GitHub and Team. GitHub installation records separate account identity, repository access, review readiness, and recovery actions, with an empty Accounts to connect section explaining how to install on another account. Team records use 32px initials avatars, visible role badges, and a trailing action menu. Editing and removal dialogs restore focus to their triggering control.

Configure opens the administrator-only repository settings page at `/repositories/<id>/settings`. Four URL-selected sections are Review behavior, AI connection, Project context, and Limits & rules, defaulting to Review behavior. The form retains edits while switching sections. Review switches pair visible labels and hints with right-aligned controls in divided rows. Other fields become two columns from 640px where appropriate. A Save action stays after the form content with visible request and validation errors; success returns to the repository list and preserves its search, filters, ordering, and loaded pages. Invalid fields in another section reveal that section and receive focus.

Project context uses a resizable textarea with a 192px minimum and 384px maximum height. Generate context and Undo sit beside its label. Missing connection/model hints explain why generation is unavailable; progress and recovery text remain beside the field. Generated context stays editable and requires Save. A stale result never overwrites edits made while generation is in progress. Unlimited limit checkboxes disable the corresponding numeric field while preserving its previous finite value; personal subscriptions show their applicable context limit and fixed concurrency with an explanation.

### Pull request details

The PR header is unframed and transparent, with no enclosing border, radius, fill, shadow, or inset padding. Its wrapping title begins with the muted PR number; the metadata row beneath contains state, author with a user icon, optional draft label, branch with a branch icon, short commit, and GitHub link. The latest-review section follows after 24px with a top divider and 16px top padding. Its label, status, and Inspect review link stay grouped above the date and Current commit or Earlier commit. The searchable connection combobox and Run review action align to the right from 640px and wrap in a full-width group below on mobile. Read-only users see an access explanation; active reviews and paused repositories have visible availability reasons below the controls and disabled review controls. Non-open PRs keep disabled review controls without an additional availability message. Fork PRs retain a manual-start explanation. Actionable review outcome and recovery guidance appear beneath the header when needed.

Findings and Run history use the shared tabs with total counts. Inbox, repository lists, findings, and assessments reuse `FilterBar` for search, the Filters menu, checkbox popovers, and removable condition chips. Finding and assessment filters remain local to the page, while Inbox and repository filters remain URL-driven. PR findings default to Open and Resolved with all severities; Dismissed remains available in Status. Status and Severity both allow multiple selections; removing a condition includes all its values. Search matches content, file path, or category without case sensitivity after the shared 300ms delay. Status choices show Open, Resolved, and Dismissed totals independently of other filters; the Findings tab announces the unfiltered total, and group headings show matching totals before pagination. Clearing search returns focus to its input. PR-specific filter choices and ordering remain in `InboxFilters`.

Findings appear as groups of individual Focused cards in order: Open, Resolved, and Dismissed. Empty groups are hidden. Each nonempty group retains a 14px semibold heading and smaller muted total count, with 10px before its cards and 24px between groups. Finding pages load separately from PR metadata and run history. Each group initially fetches up to 10 findings; its independent Load more action fetches another page of 10 and displays the shown/total count while more remain. The shared separator footer disappears when the group has no further page. Search, status, and severity apply on the server to all findings before pagination. Changing those filters resets every group to its first page. After 10 pages in a group, remaining matches prompt the user to narrow the filters.

Finding cards follow the shared location-first hierarchy without table column headings. Not observed in latest review and pending or failed synchronization remain separate notes; absence from a later review never implies resolution. Empty states distinguish no reviews, no recorded findings, no open findings, and filters with no matches. No recorded findings directs users to run completion and coverage. Where findings exist, View all findings clears search and both filters and focuses search.

Run-history rows separate Review run, Review status, and GitHub publication. The first column shows the date, trigger, short commit, model, and any attention summary; mobile publication text retains a GitHub label when column headings disappear. Whole rows link to run details and retain the originating list context. Finding cards open the evidence sheet; closing it restores focus to that card, falling back to finding search if the card is no longer mounted.

Run history fetches its first server page when the tab opens. Each page contains up to 10 runs, newest first; Load more appends the next page and the footer displays the shown/total count. The pagination footer is hidden when all runs fit on the first page; it remains visible after additional pages have been loaded. The first page refreshes every five seconds, and interval refresh pauses after loading more pages. Failed requests offer a retry without removing loaded rows; an access denial hides them. PR metadata supplies the total run count, active-review state, and latest completed or partial run independently of the history pages, preserving review availability and finding observation labels.

### Review run details

The Review run header uses the same unframed, transparent surface. Its top row pairs the title with the execution Status chip and permission-appropriate recovery actions. The PR link follows, then a wrapping metadata row with date, trigger, duration in minutes and seconds, and a commit icon beside the saved revision and Current commit or Earlier commit. Healthy completed runs omit redundant completion and next-step paragraphs; active runs and outcomes needing attention retain their guidance. Connection and model remain in Details.

GitHub comment publication and check status follow after 24px with a single top divider and 16px top padding. Partial assessment or Assessment pending sits beside the publication badge with an accessible live status description. Published and No new comments results omit the redundant confirmation sentence; other publication states, errors, and pending check updates remain explicit. Fix verification is an unbordered native disclosure row 12px below publication. Its summary keeps confirmed resolutions, pending synchronization, failed verification counts, and an inline chevron visible, with a 44px minimum height on mobile and 32px from 640px. Detailed counts and the resolution explanation expand with 8px top padding and no inset side padding. Live file progress remains visible above the tabs. Disclosure state survives polling.

Findings, Coverage, Assessment, and Details use the shared tabs, with Findings selected initially. Run findings filter saved occurrences locally using the shared filter bar for search and multiple severity selections, a matching/total count, and the shared Focused cards. Cards show saved file/line context, a three-line explanation preview, severity/category, and inline or summary comment placement. Opening a card defaults the shared evidence sheet to At this run, which contains the immutable saved explanation and code comparison. Current status and Activity explain the live finding separately. Saved evidence remains readable if that live request fails. Closing the sheet restores focus to its card or the search field.

Assessment keeps pending, incomplete, and legacy states explicit and opens candidate decisions and revision evidence in the matching side sheet. Details groups saved metadata into unboxed Review setup, Execution, and Revisions sections, with 24px between groups. Setup and execution sit side by side from 768px and stack below that width; revisions span the content width. Compact divided rows use 104px muted labels, a 16px gap, and 10px vertical padding. Values wrap within their column, and full commit hashes use 12px monospace text. Local account references read Member # followed by the saved ID. Configuration used stays collapsed by default beneath a top divider, with the same native disclosure chevron, touch target, and focus treatment as Fix verification. Its expanded content explains the saved snapshot before showing JSON. Empty findings, missing coverage, and incomplete assessment never imply a clean review.

### Coverage

Coverage begins with an unboxed 14px semibold heading, an explicit scope Status chip, and selected/completed/reused/failed/waived counts. Only validated reviewed or reused results within the selected scope count toward reviewed scope; failed, waived, and missing results remain separate. No selected files and an unrecorded legacy scope remain explicit. Selected scope reviewed does not imply a clean assessment or absence of bugs.

File groups follow the summary directly after 24px. Group counts describe the full saved report before progressive display.

Files not reviewed precedes Reviewed files, with 24px between nonempty groups. Each 14px semibold group heading has a smaller muted count and 10px before its list. Native informational list rows have 12px vertical padding, an 8px gap, a muted 16px file icon, a full wrapping 12px monospace path with 20px line height, and a Status chip at the row end. Thin semantic dividers separate rows and bound each list; rows have no card treatment or details sheet. Specific saved reasons appear inline below the path after 4px, in muted 12px relaxed text capped at 75ch; repetitive default explanations are omitted. Long paths and reasons wrap at every width.

Each group initially shows up to 10 records from the already-loaded report; independent Load more actions reveal another 10. Below the list's bottom divider, the footer has 12px top padding and no horizontal inset, with a small ghost action at the left and a live muted tabular N of M shown count at the right. It remains after more records have been revealed. Inspect coverage report is a native disclosure after 24px, with a top border and 16px top padding; its open state survives polling. Active runs without a report show recording guidance, while terminal runs explain the absence of saved evidence. Neither state implies a clean review.

### Candidate assessment

The Assessment tab begins with a 14px semibold Candidate assessment heading, a short explanation, and readable totals for new validated findings, repeated observations, withheld candidates, and published comments. Pending candidates retain a live status message. Incomplete assessment is represented by the compact Partial assessment badge beside GitHub publication in the header, with the affected count in its accessible description. No separate incomplete-assessment banner appears above the page tabs. Legacy runs explain that assessment was not recorded; active runs without assessment show Assessment pending.

The shared filter bar follows after 16px. Search matches the saved proposal content, path, or category without case sensitivity after 300ms. Result and Severity allow multiple selections; Result uses the five assessment outcomes, and Severity offers Critical, High, Medium, and Low. Choice counts describe the entire run independently of active filters. A live matching/total assessment count remains beside the toolbar. Search and filters apply on the server before pages of 10 are returned in candidate order; changing either resets the list to its first page. Opening the tab loads candidates separately from run metadata. The first page refreshes every five seconds; automatic list refresh pauses after loading additional pages.

The list uses the shared assessment cards and separator footer. Load more appends another page without removing existing cards; its count reads N of M shown. The footer is hidden when all candidates fit on the first page, remains visible after loading multiple pages, and offers Retry after a page failure or Retry assessments after a first-page failure. Duplicate candidates appear once while server totals remain intact. After 10 pages, remaining candidates prompt narrower filters. Access denial hides loaded candidates. Empty states distinguish no candidates from filters with no matches; View all assessments clears search and both filters and returns focus to search.

Assessment details use the shared Radix sheet with the Assessment details title, a wrapping filename and directory, line range, severity, and category. A bordered half-muted strip pairs an 18px clipboard-check icon with Assessment result, the outcome chip, and the reviewed commit; it explicitly separates the assessment decision from finding resolution. Assessment and Evidence tabs remain above the scrolling body, with Assessment selected initially and the evidence count beside Evidence. The selected tab survives polling.

The Assessment tab contains the outcome-specific explanation, Earlier reviews, and Changed conditions, using 14px relaxed prose capped at 75ch with 24px between sections. The Evidence tab shows saved supporting quotes labeled Original or Current with file and line context in the shared code blocks. An Original proposal disclosure remains below either tab. Pending, missing-result, missing-evidence, and error states remain explicit. Candidate details refresh every five seconds; loss of permission or a missing candidate hides its content.

The persistent bordered footer offers Open finding for linked candidates and Close for unlinked candidates, with minimum 44px targets on mobile and 36px from 640px. Open finding switches to finding details inside the same sheet, preserving the candidate and exposing Back to assessment. Closing restores focus to the originating card, falling back to the Assessment page tab when that card is no longer mounted. Candidate decisions never directly resolve or dismiss findings.

### Evidence and resolution history

The Focused evidence sheet begins with Finding details, the wrapping full file path and line range, and severity/category. A bordered, half-muted strip separates Current status from the evidence, with an 18px state icon, explicit Status chip, latest verification or preservation note, and View verification or View activity. That link selects Activity and moves focus to its tab. On mobile, the link sits beneath the status text with a 44px minimum target. PR sheets default to Evidence alongside Activity and its count; saved-run sheets add At this run and label live evidence Current status. The selected tab survives polling.

The reading area separates Explanation, code comparison, and a history note that every review keeps its original evidence, with 24px between sections. When original and suggested snippets differ and fit the comparison limit, Suggested change shows a unified syntax-colored diff, addition/removal counts, and a filename header labeled Original → Suggested. Missing, equal, or oversized snippets use labeled code blocks as available. Code regions retain full source text, 12px monospace type, horizontal scrolling, and keyboard focus; ordinary snippets use the muted surface, a 1px border, and 16px padding, while a combined diff uses the workspace background and 12px padding inside one bordered container. Syntax highlighting derives from the file language and falls back to plain text for unknown languages or snippets above 60,000 characters; diff generation uses the same combined-size limit.

Activity shows fix verifications before the event list. Verification status, reviewed commit, explanation, optional original/current evidence disclosures, automatic-resolution result, and errors remain distinct. Events use a 1px vertical border, 7px timeline dots, 20px text offsets, and 20px gaps. Actor, source, and time use muted 12px text; reasons expand under View reason. Empty, loading, and unavailable states name the missing information.

The bordered footer keeps the GitHub thread link, Resolve finding or Reopen finding, and permission-appropriate Dismiss or Keep finding open actions outside the evidence scroll. Pending confirmation retains the confirmed current state, explains the requested change, and disables the main mutation until confirmation; failed synchronization exposes Retry sync and its error. On mobile, resolution actions precede the full-width GitHub link, with minimum 44px targets; from 640px they align horizontally with 36px targets. Dismissal expands an autofocus reason field and Confirm dismissal in the footer, requires a nonempty reason, and limits it to 2,000 characters. Read-only users retain evidence and history without mutation controls.

### Overlays and motion

Radix dialogs and sheets retain their accessible interaction behavior and close controls. Dialog transitions use 200ms; sheets open in 500ms and close in 300ms. Desktop sidebar position and main-column gap transitions use 200ms linear easing. Shared control transitions use the standard easing. Reduced-motion preference shortens animation and transition duration to 0.01ms and disables smooth scrolling. Do not add motion to static evidence.

Popover, Checkbox, and DropdownMenu are added through the shadcn CLI using Bun. Their generated primitives wrap Radix; inbox-specific sizing and positioning are supplied through props. Filter popovers render in a portal, align to the trigger start with an 8px offset, keep 16px viewport collision padding, and cap their width at the viewport minus 32px. Filter choices scroll within a 256px maximum height. Opening and closing use fade and scale animations, with directional movement on opening. Keyboard activation, Escape dismissal, outside interaction, and focus return retain Radix behavior; clearing a condition or dismissing an unused filter returns focus to Filters. Closing an active filter returns focus to its property button. Ordering uses radio menu items with arrow-key navigation, checked state, Escape dismissal, and focus return. Checkbox rows are at least 40px tall and expose focus around the full label. The final selected PR state cannot be unchecked, keeping the selection meaningful.

## Do's and Don'ts

### Do:

- Do use semantic CSS properties so light, dark, and system appearance stay consistent.
- Do keep status wording visible alongside color, including partial coverage and publication failures.
- Do retain clear focus indicators, associated field labels, and reduced-motion behavior.
- Do wrap long prose and filenames appropriately while allowing code blocks to scroll horizontally.
- Do keep finding previews to three lines and 75ch while preserving the full explanation in the details sheet.
- Do distinguish saved review evidence, current resolution, not-observed history, and pending GitHub synchronization.
- Do keep finding tabs and resolution actions outside the scrolling evidence area, and restore focus to the card when the sheet closes.
- Do reuse the Focused card and right-sheet structure for assessments, keeping assessment outcomes separate from finding resolution.
- Do keep finding and assessment pagination aligned with their cards, with 16px above the separator and 12px below it.

### Don't:

- Don't add decorative eyebrow labels above page titles; retain functional repository and pull-request breadcrumbs.
- Don't replace native selects or the existing shadcn primitives without a concrete interaction need.
- Don't imply a finding is resolved merely because it is absent from a later review.
- Don't wrap pull-request or review-run page headers in cards.
- Don't replace the compact partial-assessment badge with a separate warning banner.
- Don't introduce a mascot, approved metaphor, or additional brand assets as if they were already established.
