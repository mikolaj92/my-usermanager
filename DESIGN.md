# my-usermanager Design System

This design system is the source of truth for the planned FastAPI, Jinja, HTMX, and Basecoat CDN proof. It defines the product-facing UI layer only. The Python core remains generic and must not require UI, template, HTMX, Basecoat, or browser dependencies.

## 1. Atmosphere & Identity

my-usermanager should feel like a quiet operations console for authentication and user administration: clear, restrained, and reliable under load. The signature is warm utility: off-white work surfaces, crisp bordered cards, compact data rows, and plain language that helps maintainers understand users, grants, sessions, and auth actions without decorative noise.

## 2. Color

Use Basecoat tokens and semantic classes first. If a token is unavailable in Basecoat, map the role to the closest CSS custom property in the app shell rather than introducing a separate CSS framework. Color is functional, not decorative.

### Palette

| Role | Token | Light | Dark | Usage |
|------|-------|-------|------|-------|
| Surface/base | `--background` | `#F8F5EF` | `#11100E` | Page canvas, warm off-white background |
| Surface/card | `--card` | `#FFFCF7` | `#191714` | Cards, panels, auth forms |
| Surface/muted | `--muted` | `#F0ECE4` | `#24211D` | Subtle sections, table headers, loader tracks |
| Text/primary | `--foreground` | `#1F1D1A` | `#F6F2EA` | Body copy, headings, primary labels |
| Text/secondary | `--muted-foreground` | `#6F6860` | `#AFA79D` | Help text, metadata, secondary actions |
| Border/default | `--border` | `#DED7CB` | `#34302A` | Card outlines, dividers, input borders |
| Border/subtle | `--input` | `#E8E1D6` | `#3E3932` | Form controls, quiet separators |
| Accent/primary | `--primary` | `#25221E` | `#F6F2EA` | Primary buttons, active navigation, focused intent |
| Accent/on-primary | `--primary-foreground` | `#FFFCF7` | `#191714` | Text on primary actions |
| Accent/secondary | `--secondary` | `#ECE6DA` | `#29251F` | Secondary buttons, tabs, chips |
| Status/success | `--success` | `#2F6F4E` | `#7DD3A8` | Successful saves, accepted invites |
| Status/warning | `--warning` | `#8A5A14` | `#F2C46D` | Pending invites, expiring grants |
| Status/error | `--destructive` | `#A64235` | `#FFB4A8` | Validation errors, destructive actions |
| Focus/ring | `--ring` | `#8B7E6B` | `#BBAE9A` | Keyboard focus and form focus rings |

### Rules

- Prefer Basecoat utilities such as `bg-background`, `bg-card`, `text-foreground`, `text-muted-foreground`, `border`, `border-border`, `bg-primary`, and `text-primary-foreground` where available.
- The warm off-white canvas is the default. Do not use bright full-page color blocks, gradients, neon accents, or decorative color washes.
- Accent color is reserved for state, focus, and user intent. Links and CTAs may use primary foreground treatment; content cards should stay neutral.
- Never introduce raw color values in templates except to define or document tokens. Extend this table first if a new semantic role becomes necessary.

## 3. Typography

The UI should read like system documentation attached to an admin console: precise, compact, and easy to scan.

### Scale

| Level | Size | Weight | Line Height | Tracking | Usage |
|-------|------|--------|-------------|----------|-------|
| Page title | `2rem` | 650 | 1.15 | `-0.02em` | Authentication and admin page titles |
| Section title | `1.375rem` | 620 | 1.25 | `-0.01em` | Card groups, page sections |
| Card title | `1rem` | 600 | 1.35 | `0` | Operational card headings |
| Body | `0.9375rem` | 400 | 1.55 | `0` | Default copy, table cells, form text |
| Body small | `0.875rem` | 400 | 1.45 | `0` | Helper text, row metadata |
| Label | `0.8125rem` | 550 | 1.35 | `0.01em` | Form labels, field names |
| Caption | `0.75rem` | 500 | 1.35 | `0.04em` | Status labels, audit metadata, badges |
| Code/meta | `0.8125rem` | 500 | 1.45 | `0` | IDs, scopes, provider names, timestamps |

### Font Stack

- Primary: `ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`
- Mono: `ui-monospace, "SFMono-Regular", "SF Mono", Consolas, "Liberation Mono", monospace`

### Rules

- Use system fonts to avoid mandatory font dependencies and keep the proof CDN-light.
- Body text must not render below `14px`.
- Use monospace sparingly for identifiers, provider keys, grant scopes, request IDs, and timestamps.
- Avoid marketing language. Use direct operational copy: "Invite user", "Grant pending", "Session expires", "Provider subject".

## 4. Spacing & Layout

All spacing derives from a 4px base unit and should map to Basecoat-compatible utility spacing. Layouts are compact but not cramped.

### Spacing Tokens

| Token | Value | Usage |
|-------|-------|-------|
| `--space-1` | `4px` | Icon-to-label gap, tight inline metadata |
| `--space-2` | `8px` | Form helper gap, compact row gaps |
| `--space-3` | `12px` | Input padding, badge groups, table cell rhythm |
| `--space-4` | `16px` | Default card padding on mobile, field groups |
| `--space-5` | `20px` | Card padding on wider screens, auth form groups |
| `--space-6` | `24px` | Section stacks, card group gaps |
| `--space-8` | `32px` | Page header to content, major panel groups |
| `--space-10` | `40px` | Top-level page padding on desktop |
| `--space-12` | `48px` | Auth shell vertical rhythm |

### Layout

- Page shell: centered content with `max-width: 1120px`, responsive horizontal padding from `16px` to `32px`.
- Auth shell: single column, `max-width: 420px`, vertically centered only when content still fits comfortably on small screens.
- Admin shell: header, optional narrow navigation region, and main content grid. Prefer one column on mobile and two columns only when both columns carry operational value.
- Cards: use consistent border radius and internal spacing; do not nest cards inside cards unless the inner element is a genuine interactive region.
- Tables and lists: dense rows are acceptable, but each row needs enough vertical rhythm for focus outlines and HTMX loading states.

### Rules

- No magic spacing values in templates. Use Basecoat utilities or these tokens.
- Prefer stable layouts for HTMX swaps. Reserve space for loaders, validation messages, and empty states to avoid layout jumps.
- Do not introduce a custom grid framework. Native CSS layout and Basecoat utilities are enough.

## 5. Components

Components are server-rendered HTML patterns using Basecoat classes, Jinja templates, and HTMX attributes. They must remain optional examples around the generic package, not mandatory runtime requirements.

### App Shell

- **Structure**: `body` with warm `bg-background`, a constrained `main`, and a simple header containing product name, current subject, and sign-out action when authenticated.
- **Variants**: unauthenticated auth shell; authenticated admin shell.
- **Spacing**: `--space-6` page stack on mobile, `--space-8` to `--space-10` on desktop.
- **States**: anonymous, signed in, signed out, loading after HTMX navigation.
- **Accessibility**: one `main` landmark, clear page heading, skip link if navigation grows.
- **Motion**: optional opacity transition on swapped content only.

### Operational Card

- **Structure**: Basecoat card container with title, short description or metadata, primary content, and optional footer actions.
- **Variants**: summary, form, list, warning, destructive confirmation.
- **Spacing**: `--space-4` on mobile, `--space-5` or `--space-6` on desktop.
- **States**: default, hover for clickable cards, focus-visible, loading, empty, error.
- **Accessibility**: clickable cards must expose a real link or button, not a `div` click handler.
- **Motion**: hover may use opacity or a tiny `translateY(-1px)` transform; no layout animation.

### Forms

- **Structure**: semantic `form`, visible labels, Basecoat input/select/textarea/button classes, inline help text, and a validation message region.
- **Variants**: sign in, invite user, edit user, grant role, revoke confirmation.
- **Spacing**: field stack `--space-4`; label-to-input `--space-2`; action row `--space-5`.
- **States**: default, focused, disabled, invalid, submitting, success.
- **Accessibility**: `for`/`id` label pairing, `aria-describedby` for help and errors, server-rendered errors after HTMX swaps.
- **Motion**: submit indicators may fade in/out; do not animate field size or position.

### Buttons and Links

- **Structure**: native `button` or `a` elements using Basecoat button variants.
- **Variants**: primary, secondary, ghost, destructive, link.
- **Spacing**: compact horizontal rhythm; align action rows to the content edge.
- **States**: hover, active, focus-visible, disabled, loading.
- **Accessibility**: destructive actions use explicit text and confirmation when risk is high.
- **Motion**: active press may use a small transform; loading state uses opacity and `aria-busy` where appropriate.

### Data Lists and Tables

- **Structure**: semantic `table` for tabular data; `ul`/`li` for event feeds or compact resource lists.
- **Variants**: users, grants, sessions, provider subjects, audit events.
- **Spacing**: row padding `--space-3` to `--space-4`; group gap `--space-2`.
- **States**: empty, filtered empty, loading, stale, error.
- **Accessibility**: table headers for tabular data; row actions must be keyboard reachable.
- **Motion**: HTMX row replacement may fade opacity only.

### Status Messages and Badges

- **Structure**: concise text label with semantic color token and optional accessible description.
- **Variants**: success, warning, error, info, pending, disabled.
- **Spacing**: badge internal padding maps to `--space-1` and `--space-2`.
- **States**: static, updating, dismissible alert.
- **Accessibility**: errors use `role="alert"` when inserted after submission; status updates use polite live regions when non-blocking.
- **Motion**: inserted messages may fade in with opacity only.

### HTMX Loading Region

- **Structure**: target container with stable dimensions, `aria-busy`, and a Basecoat-compatible loader or text indicator.
- **Variants**: button-level, form-level, card-level, table-row-level.
- **Spacing**: reserve `--space-4` minimum for messages and spinners.
- **States**: idle, loading, settled, error.
- **Accessibility**: loading text must be available to assistive technology; do not rely on animation alone.
- **Motion**: show and hide indicators with opacity or visibility only.

## 6. Motion & Interaction

Motion is quiet feedback for server-rendered interactions. It should never become a dependency or mask slow operations.

### Timing

| Type | Duration | Easing | Usage |
|------|----------|--------|-------|
| Micro | `100ms` to `150ms` | `ease-out` | Button press, focus reveal, hover feedback |
| Standard | `180ms` to `220ms` | `ease-in-out` | HTMX fragment fade, alert insertion |
| Reduced | `0ms` | none | `prefers-reduced-motion: reduce` |

### Rules

- Animate only `opacity` and `transform`.
- HTMX loaders may toggle visibility, opacity, or `aria-busy`; do not animate height, width, margins, or table layout.
- Every interactive control needs visible hover, active, disabled, and keyboard focus states.
- Respect `prefers-reduced-motion` by removing non-essential transitions.
- Do not add animation libraries for the proof. Native CSS and HTMX state classes are enough.

## 7. Depth & Surface

The depth strategy is borders with subtle tonal shifts. Shadows are reserved for overlays only, and even then they should be faint.

### Surface Levels

| Level | Treatment | Usage |
|-------|-----------|-------|
| Canvas | `bg-background` warm off-white | Whole page and auth shell backdrop |
| Card | `bg-card border border-border` | Forms, summaries, resource cards |
| Muted | `bg-muted text-muted-foreground` | Table headers, helper regions, disabled surfaces |
| Raised | `bg-card border border-border` plus faint shadow only if needed | Menus, popovers, confirmation dialogs |
| Destructive | Neutral card with `--destructive` text or border emphasis | Revoke, delete, disable, sign-out confirmation |

### Rules

- Default separation is a one-pixel border and a tonal background change, not shadow.
- Border radius should stay crisp: `6px` for controls, `8px` to `12px` for cards and dialogs.
- Do not use glassmorphism, heavy drop shadows, nested decorative shells, or gradient panels.
- Operational density is acceptable when hierarchy is clear: title, metadata, action, status.
- Empty and error surfaces should look intentional and calm, using the same card and border language as normal states.
