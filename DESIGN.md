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

## Colors

The palette combines forest-green actions and navigation accents with warm green-tinted neutrals. Frontmatter holds the actual light values and matching `dark-` values; the app switches the semantic CSS properties together.

### Primary

Primary green identifies consequential actions, links, the sidebar brand, and repository activity. The lighter dark-theme primary keeps those elements readable against dark surfaces.

### Neutral

Background is the workspace canvas; card and popover distinguish bounded content. Foreground carries reading text, muted foreground carries supporting information, and border/input separate surfaces and controls. Secondary and accent are related green-tinted interaction surfaces rather than additional brand accents.

Sidebar colors reuse the workspace palette in both themes: `--sidebar` aliases `--muted`, `--sidebar-foreground` aliases `--foreground`, and the sidebar primary, primary-foreground, accent, accent-foreground, border, and ring properties alias their matching semantic tokens. Selected and hovered links use the filled accent surface; repository owner labels and icons use muted foreground.

Status colors retain their Tailwind OKLCH source values. Good states use emerald, failures and high severity use red, and partial, blocked, outdated, and medium-severity states use amber. Running status chips use blue with their spinner; the sidebar's review activity spinner uses primary green. Not reviewed, cancelled, superseded, and other statuses remain neutral. Status fills use 8% opacity and borders 20%; dark mode changes the text tone. Destructive actions and errors use the semantic destructive token.

## Typography

Inter Variable is the interface family, with sans-serif fallback. There is no separate display face. Platform monospace is reserved for code, paths, branches, and identifiers.

Page headlines use `headline`, changing to `headline-wide` at the small breakpoint. Inbox and repository titles use `headline-wide` at every width. Standard content and buttons use body-sized type; section headings commonly use 16px semibold, while inbox group headings use 14px semibold. Labels use medium weight and metadata uses the smaller role. Descriptive paragraphs use relaxed line height (1.625); Markdown review prose uses 1.7. Counts use tabular numerals. Do not add decorative uppercase labels.

## Layout

At 1024px and wider, the shell has a fixed 256px sidebar and a flexible main column. The sidebar collapses completely offscreen and releases its full width to the main column. Below 1024px, navigation opens as a 288px left drawer with a backdrop. A sticky 56px header keeps the navigation toggle, page breadcrumbs, and far-right appearance toggle available at every width. Main content has a centered 1152px cap, 48px horizontal and 40px vertical padding on desktop; below 1024px it uses 20px horizontal and 32px vertical padding, increasing to 36px horizontal at 640px. Sidebar content scrolls independently between its brand header and footer.

Each inbox list groups rows within its own bordered surface, with 24px between lists. Typical row padding is 20px; filters and actions wrap with 8px or 12px gaps. At 640px, repository controls receive bounded widths, pull-request status moves to the row end, and connection rows become horizontal. Inputs use 16px text below 768px and 14px above it. Forms retain visible labels and hints.

The inbox uses a wrapping toolbar adapted from Grudget’s subscriber filters: compact search, a Filters menu, active filter buttons, and ordering controls aligned to the right when space permits. Search fills the mobile width and uses 224px from 640px. The result count sits below. At 768px and wider, a shared grid aligns pull-request details, review status, open findings, creation date, and a chevron. The first column flexes; the others use 160px, 104px, 96px, and 16px, with 16px gaps. Below 768px, column headings disappear, the title and repository metadata span the row, status and findings share the next line, and the date sits below. The chevron remains at the upper right. Titles wrap, repository and author identifiers can break, and selected filter summaries truncate within their controls. Rows use 16px padding on mobile and 20px horizontal/12px vertical padding from 768px.

Finding details occupy a right sheet, full width on small screens and capped at 672px from 640px. Repository configuration is also full width on small screens, with a 576px cap from 640px. Sheets scroll vertically. Dialogs keep 16px outer margins; connection editing is capped at 576px and 90dvh. Setup uses a centered 512px column.

Finding and run-history rows share a grid: at 768px, a flexible reading column is followed by 104px and 160px state columns and a 16px chevron, with 16px gaps. Below 768px, headings and chevrons disappear, the reading column spans the row, and the two state fields sit beneath it. Rows use 20px horizontal and 16px vertical padding. The shared filter bar search fills the mobile width and uses 224px from 640px; filter chips and review controls wrap.

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

### Chips

Status chips use 12px medium text, 2px/8px padding, thin borders, and semantic tints. Text is capitalized and underscores become spaces. Queued/running states include a spinner only when motion is allowed. These chips communicate state and are not filter buttons.

The Filters button uses the installed shadcn dropdown menu and lists properties without an active condition. Choosing a property opens its checkbox popover. Only active filters and the property currently being edited appear in the toolbar; the all-state default has no active PR state condition. Dismissing a new filter without a selection leaves it in the menu. Active filter controls match the Filters button with a solid 1px outline, medium radius, and small shadow; dark mode uses the input border token. They retain the card surface, plus-circle icon, and property label. The inner hover edges where the filter and clear button meet are square. Selected values appear as compact secondary badges after a vertical separator: up to two labels on larger screens, a count for more selections, and a count on mobile. A separate sibling clear button removes the condition without nesting interactive controls. The popover also offers Clear filters or Include all states. Clearing removes the filter from the toolbar, makes it available in the menu again, and returns focus to Filters. Long selected values truncate; their full names remain available in the accessible label and option list.

Inbox search, selected filters, ordering, and loaded group pages are represented in the URL. Filters cover Repository, PR state, Review status, Author, and Open findings. Repository and Author options are searchable; checkbox choices support multiple values except Open findings, which accepts one. The default PR state selection includes Open, Merged, and Closed. The PR state filter narrows that selection, and each state shows its matching count. Running reviews remain visible regardless of PR state, while respecting the other filters. Clearing that condition includes all states. The toolbar has no global Reset button; individual filter, search, and ordering clear actions remain available. The no-matches empty state offers a filter reset. Search updates after 300ms, and browser navigation restores the URL's selections. A separate dropdown selects Created, Updated, Title, Repository, or Author; the adjacent arrow reverses direction and exposes its current meaning in a tooltip. The URL uses `ordering` with a leading minus for descending order, defaulting to `-created_at`. Dates initially sort descending and text ascending. Reset ordering returns to newest first. Ordering and filter changes reset all loaded page counts.

### Cards / Containers

Content cards use the card surface, large radius, a 1px border, and usually 20px padding. Lists share a single container with divided rows; interactive rows use a 50% muted hover fill. Empty states use dashed borders and centered guidance.

### Navigation

The sidebar uses the shadcn Sidebar primitive installed through the CLI with Bun. Links combine 16px outline icons, 14px text, an 8px gap, and 8px padding. Inbox and Settings rows are 40px high; repository rows are 48px. Active links retain a filled accent background and accent foreground; hover uses the same treatment. Repository rows place the name above its owner in 12px muted text, truncate both within the available width, and expose the full `owner/name` through their accessible label and native title. Repository rows do not force hover tooltips in the expanded sidebar or mobile drawer. A repository with a running review shows only a green 12px spinner that animates only when motion is allowed; its accessible link label includes review running. Loading, empty, and retry states appear inside the repository group. Access-denied responses hide repository data and explain the permission failure without a transient-error retry action.

Inbox is the homepage at `/`; repository links open `/repositories/<id>`. Legacy `/repositories?repo=<id>` links redirect to that path and retain configuration parameters; `/repositories` opens the first available repository. An unavailable ID never silently selects another repository. The repository page uses the full `owner/name` as its wrapping title, with selection handled in the sidebar. Administrators get a Manage repositories action beside the group label. Settings at `/settings` stays above the account row in the footer. The 48px account row uses the shadcn Avatar installed with Bun: a 32px rounded-square GitHub avatar, a two-letter initials fallback, the GitHub username, and a muted Administrator or Member label. The full row opens an upward dropdown through a trailing three-dot affordance; Settings and Sign out live in that menu, with Settings also remaining directly available in the sidebar. During sign-out, the row is disabled and shows Signing out with a reduced-motion-aware spinner. The footer has no separating top border. The compact `cm` mark is typographic, not a mascot; its brand link returns to the unfiltered inbox.

The authenticated workspace header uses the shadcn Breadcrumb primitive installed through the CLI with Bun. Paths begin with Inbox, then the repository, Pull request #N, and Review run as applicable; settings show Inbox, Settings, and the active tab. Loading or failed lookups use generic page labels. Ancestor links use muted 14px text, chevron separators, and foreground hover; the current page uses medium-weight foreground text and exposes its current state. Below 640px, intermediate ancestors move into a shadcn dropdown opened by an ellipsis button, keeping Inbox and the current page visible. Long labels truncate with their full text available in titles; dropdown labels wrap.

Pull and run detail routes select the matching repository in the sidebar, including when reached from Inbox. Inbox is active only on the inbox page. The Inbox sidebar link and header breadcrumb restore the originating inbox URL, including its filters and loaded pages. When opened from a repository list, the matching repository sidebar link and breadcrumb restore that list's URL, filters, ordering, and loaded pages. Pull and run links preserve either return context. Choosing a sidebar link closes the mobile drawer. The persistent header navigation toggle exposes its expanded state and supports Command/Ctrl+B outside editing controls. The collapsed desktop panel is inert; collapsing it with the keyboard while focus is inside moves focus to the toggle. The mobile drawer retains sheet focus trapping and Escape dismissal, includes a visible Close navigation button, and restores focus to the header toggle when closed.

### Grouped PR inbox

Separate bordered lists appear in order: Open, Merged, and Closed. The default view includes all PR states, including reviewed open PRs. Only nonempty lists appear. A single visible list has no section heading; multiple lists have static 14px semibold labels and smaller muted total counts above their bordered surfaces, with 10px between each label and its list. Section labels have no filled header bar or collapse control. Lists retain accessible region names even without visible headings. Each included state has its nonempty list with column labels and an independent Load more action. A PR with any running review stays in Open until that run finishes, even if the PR has merged or closed; it never appears in two lists. Those rows display their actual PR state. If the running run is on an older revision, the current-revision review status remains accurate and a separate note explains that a review is still running. Completed reviews of open PRs remain in the same list. Active work places running reviews first, then queued reviews, then the remaining statuses; rows within each priority default to GitHub creation date, newest first, with a stable ID tie-breaker. The selected ordering applies within each priority and group, before pagination; text ordering is case-insensitive. Edits and background syncs do not promote older PRs. Existing records without a creation date appear last within their priority and show Pending sync until synchronization fills the date. Each group initially shows up to 10 PRs, and Load more adds another 10. Groups load more independently and keep pagination failures and retry actions beside the affected group.

Whole rows link to PR details and carry the inbox URL for the return path. All PR rows use the same card background, including running reviews; hover uses the shared muted fill. Review status, publication failures, and open finding counts remain distinct. Publication failed and Partially published appear immediately after the username in row metadata, using destructive and amber warning text respectively. Finding badges read “N unresolved” so they cannot be mistaken for the PR state. Positive finding counts use destructive text and a restrained tint; zero uses muted styling. Reviewed open PRs can still contain open findings. Empty results distinguish an empty workspace from filters with no matches. Resetting filters restores all PR states. The combined list retains `active_pages` in the URL; legacy `reviewed_pages` is accepted when `active_pages` is absent.

### Repository pull requests

The repository header pairs its wrapping name with visibility and a GitHub link, plus Sync and an administrator-only Configure action. A compact strip with top and bottom borders shows whether reviews are enabled or paused. Enabled repositories show automatic or manual review policy, the file limit, and comment language; paused repositories show permission-appropriate guidance. The strip wraps on mobile and precedes the Pull requests heading and shared Inbox toolbar.

Repository lists reuse Inbox's filters, responsive row grid, status chips, and unresolved-finding badges. Repository scope is fixed by the page, so the toolbar offers PR state, Review status, Author, and Open findings filters. Search, filters, ordering, and loaded group pages live in the URL. Ordering offers Updated, Title, and Author, defaulting to Updated newest first; the date column is Updated. Running reviews come first, then current-revision queued reviews, with the selected order applied within each priority before pagination. Row metadata retains the PR number, author, branch, draft and fork labels, and actionable review feedback. Reviews on an older revision show Outdated, with a separate note if that review is still running.

Open, Merged, and Closed are included by default. Each nonempty group keeps its heading and total count, starts with up to 10 rows, and has an independent Load more action adding 10 at a time. Running reviews remain in Open regardless of PR state and retain their actual state badge. Each group displays at most 100 rows before asking the user to narrow the filters. This is client-side display pagination: the repository endpoint still fetches all of that repository's PR rows. Filter and ordering changes reset the loaded pages, while row links preserve the repository return context.

### Pull request details

The PR header shows the wrapping title above its state, number, author, optional draft label, branch, short commit, and GitHub link. A strip with top and bottom borders pairs the latest review status and date with Current commit or Earlier commit, an Inspect review link, and the native connection selector and Run review action. Read-only users see an access explanation; active reviews, non-open PRs, and paused repositories have visible availability reasons and disabled review controls. Fork PRs retain a manual-start explanation. Actionable review outcome and recovery guidance appear beneath the strip when needed.

Findings and Run history use the shared tabs with total counts. Inbox, repository lists, and findings reuse `FilterBar` for search, the Filters menu, checkbox popovers, and removable condition chips. Finding filters remain local to the page, while Inbox and repository filters remain URL-driven. Findings start with an active Status: Open condition. Status and Severity both allow multiple selections; removing a condition includes all its values. Search matches content, file path, or category without case sensitivity after the shared 300ms delay. Status choices show Open, Resolved, and Dismissed totals independently of other filters; a separate live count shows the visible findings out of the total. Clearing search returns focus to its input. PR-specific filter choices and ordering remain in `InboxFilters`.

Finding rows separate the two-line explanation preview, truncated file and line range, and category from severity and resolution Status chips. The full file location remains available in its title. Not observed in latest review and pending or failed synchronization remain separate notes; absence from a later review never implies resolution. Empty states distinguish no reviews, no recorded findings, no open findings, and filters with no matches. No recorded findings directs users to run completion and coverage. Where findings exist, View all findings clears search and both filters and focuses search.

Run-history rows separate Review run, Review status, and GitHub publication. The first column shows the date, trigger, short commit, model, and any attention summary; mobile publication text retains a GitHub label when column headings disappear. Whole rows link to run details and retain the originating list context. Finding rows open the evidence sheet; closing it restores focus to that row, falling back to finding search if the row is no longer mounted.

### Evidence and resolution history

Finding details separate Markdown explanation, original code, suggested change, actions, and Activity. Code blocks use a muted background, a 1px border, 16px padding, and 12px monospace text. The Activity list uses 2px left borders, 16px inner offsets, and 20px gaps; actor, source, time, and reason remain distinct. Pending GitHub confirmation and synchronization errors remain visible beside actions.

### Overlays and motion

Radix dialogs and sheets retain their accessible interaction behavior and close controls. Dialog transitions use 200ms; sheets open in 500ms and close in 300ms. Desktop sidebar position and main-column gap transitions use 200ms linear easing. Shared control transitions use the standard easing. Reduced-motion preference shortens animation and transition duration to 0.01ms and disables smooth scrolling. Do not add motion to static evidence.

Popover, Checkbox, and DropdownMenu are added through the shadcn CLI using Bun. Their generated primitives wrap Radix; inbox-specific sizing and positioning are supplied through props. Filter popovers render in a portal, align to the trigger start with an 8px offset, keep 16px viewport collision padding, and cap their width at the viewport minus 32px. Filter choices scroll within a 256px maximum height. Opening and closing use fade and scale animations, with directional movement on opening. Keyboard activation, Escape dismissal, outside interaction, and focus return retain Radix behavior; clearing a condition or dismissing an unused filter returns focus to Filters. Closing an active filter returns focus to its property button. Ordering uses radio menu items with arrow-key navigation, checked state, Escape dismissal, and focus return. Checkbox rows are at least 40px tall and expose focus around the full label. The final selected PR state cannot be unchecked, keeping the selection meaningful.

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
