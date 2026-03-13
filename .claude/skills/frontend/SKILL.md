---
name: frontend
description: >
  Work on the Druids frontend (Vue 3 + Vite). Use this when the user asks to
  build, modify, or fix anything in the `frontend/` directory. Covers the
  design system, component conventions, and the rodney-based visual iteration
  workflow.
user-invocable: false
---

# Druids Frontend

The Druids frontend is a Vue 3 single-page application in `frontend/`. It uses Vite for development and builds, vue-router with HTML5 history mode, and a plain `fetch` wrapper for API calls (`frontend/src/api.js`). There are no CSS frameworks or component libraries. All styling is hand-written in `frontend/src/style.css` with scoped styles in individual `.vue` files where needed. All routes are clean paths (`/settings`, `/guide`); there are no hash fragments.

## Stack

- Vue 3 with `<script setup>` composition API (no options API)
- Vite 7 dev server with hot module replacement
- vue-router 4 with `createWebHistory` (HTML5 history mode)
- No TypeScript, no build-time type checking
- No state management library; components use local `ref`/`computed` state and fetch data in `onMounted`

## Project layout

```
frontend/
  index.html
  package.json
  vite.config.js          # proxies /api to localhost:8000
  src/
    main.js               # creates app, mounts router
    router.js             # route definitions, auth guard
    api.js                # fetch wrapper: get(), post(), del()
    auth.js               # useAuth composable (user state, login/logout)
    style.css             # global styles, design system
    App.vue               # shell: sidebar + router-view
    pages/
      HomePage.vue        # dashboard with executions table
      ProgramsPage.vue    # execution detail with terminal
      SettingsPage.vue    # API key management
      UsagePage.vue       # admin usage stats
      GuidePage.vue       # getting started guide
      LoginPage.vue       # login page
      LandingPage.vue     # unauthenticated landing
  screenshots/            # reference screenshots (desktop + mobile)
```

## Design system

The visual language is warm, understated, and monospaced. Dotted borders, earth tones, no drop shadows, no gradients, no rounded corners beyond `2px`. The aesthetic is closer to a terminal or a printed ledger than a SaaS dashboard.

### CSS variables (defined in `style.css :root`)

```
--bg: #ded5bd              warm parchment background
--bg-terminal: #d3c9af     slightly darker, used for code blocks
--bg-card: transparent      cards are borderless by default
--bg-card-hover             subtle hover tint
--text: #3a3530            primary text, dark brown
--text-secondary: #6b6356  muted labels
--text-bright: #1a1714     headings, emphasis
--text-dim: #9a8f7f        timestamps, tertiary
--border: rgba(0,0,0,0.15) dotted borders
--border-light              lighter variant
--green: #2d7a3e           success, active
--red: #a3403a             error, danger
--yellow: #8a6b1a          warning
--font-mono                Berkeley Mono, SF Mono, Fira Code, monospace
--font-serif               Source Serif 4, Georgia, serif
```

### Typography

- Body text uses `--font-mono` at `clamp(0.82rem, 2vw, 0.88rem)`
- Headings (`h1`, `h2`, `h3`) use `--font-serif`, italic, normal weight
- Labels and metadata are uppercase, letter-spaced, `0.68-0.72rem`

### Component classes

The global stylesheet provides these reusable classes. Use them instead of writing one-off styles:

- `.card`, `.card-grid` -- bordered containers
- `.btn`, `.btn-primary`, `.btn-secondary`, `.btn-danger`, `.btn-ghost`, `.btn-sm` -- buttons
- `.badge`, `.badge-active`, `.badge-completed`, `.badge-error` -- status indicators
- `.terminal`, `.terminal-output`, `.terminal-input` -- terminal-style panels
- `.data-table` -- tables with dotted row separators
- `.mobile-cards`, `.mobile-card` -- card layout shown below 720px (tables hide)
- `.page-header` -- page title + subtitle
- `.stat-card`, `.stat-label`, `.stat-value` -- metric display
- `.empty-state` -- centered placeholder
- `.spinner` -- loading indicator
- Utilities: `.text-secondary`, `.text-bright`, `.text-dim`, `.text-green`, `.text-red`, `.mt-1` through `.mt-3`, `.mb-1` through `.mb-3`, `.flex`, `.items-center`, `.justify-between`, `.gap-1`, `.gap-2`

### Responsive behavior

At `max-width: 720px`, the sidebar collapses to a horizontal nav bar, tables are hidden and replaced by `.mobile-cards`, and padding shrinks. New components must include both desktop and mobile layouts following this pattern.

## Component conventions

- Use `<script setup>` with imports from `vue` and `../api.js`
- Fetch data in `onMounted`, store in `ref`s, derive with `computed`
- Use the global CSS classes from `style.css`. Only add `<style scoped>` for truly component-specific layout (e.g., a custom form arrangement)
- Inline styles are acceptable for one-off spacing or sizing that doesn't warrant a class
- No emojis in the UI. Status is communicated through text, color, and badges
- Router links use `<router-link :to="...">`, not `<a href>`

## Starting the dev server

The Vite dev server runs from `frontend/` on port 5173 by default. It proxies `/api` requests to `localhost:8000` (the Druids server). To work on the frontend without the backend running, you can still load the app, but API calls will fail.

```
cd frontend && npm run dev
```

The server supports hot module replacement. Saving a `.vue` or `.css` file updates the browser automatically. You do not need to restart the dev server after edits.

## Visual iteration with rodney

`rodney` is a Chrome automation CLI already installed on this machine. You have no display, so rodney is how you see what the page looks like. The workflow is: edit code, screenshot, read the screenshot, evaluate, repeat.

You must use rodney for visual verification. Do not guess what the page looks like from the HTML alone. Screenshots catch CSS issues, layout problems, and visual regressions that are invisible in source code.

### Starting the browser (once per session)

On this devbox (arm64), `rodney start` cannot find a Chrome binary. Launch Chromium manually and point rodney at it:

```bash
chromium-browser --headless --no-sandbox --disable-gpu --remote-debugging-port=9222 &
rodney connect 127.0.0.1:9222
```

On machines where `rodney start` works (x86, non-snap Chrome), that single command is enough. Try it first; fall back to the manual approach if it fails.

Once connected, navigate to the dev server:

```bash
rodney open http://localhost:5173
rodney waitload
```

Run `rodney status` to confirm the browser is connected and see the current URL.

### The edit-screenshot-evaluate loop

This is the core workflow. After every meaningful edit:

```bash
rodney reload --hard              # pick up changes (HMR usually handles this, but --hard is reliable)
rodney waitload                   # wait for the page to finish loading
rodney screenshot                 # prints the file path, e.g. /home/ubuntu/.rodney/screenshots/shot-001.png
```

The `screenshot` command prints the path where the image was saved. Use the Read tool on that path to view it. This is how you see the page. Do not skip this step.

For mobile layout verification, set a narrow viewport:

```bash
rodney screenshot -w 375 -h 812   # iPhone-sized viewport
```

If something looks wrong, fix the code and screenshot again. Do not move on until the page looks correct at both desktop and mobile widths.

### Quick structural checks (no screenshot needed)

Screenshots are the gold standard for visual verification, but lighter commands are faster for structural checks:

```bash
rodney ax-tree                    # dump the accessibility tree -- shows headings, links, buttons, text
rodney ax-tree --depth 3          # limit depth for large pages
rodney text ".page-header h1"     # get text content of a specific element
rodney html ".card-grid"          # get the HTML of a section
rodney count ".data-table tr"     # count table rows
rodney exists ".empty-state"      # exits 0 if found, 1 if not
rodney visible ".spinner"         # exits 0 if visible, 1 if hidden or absent
```

Use `ax-tree` when you need a quick overview of what is on the page -- it lists every heading, link, button, and text node in a tree. It is faster than a screenshot and useful for verifying that new elements appear in the DOM after an edit.

Use `exists` and `visible` for conditional checks. They communicate through exit codes: 0 means yes, 1 means no.

### Element screenshots

To screenshot a single component instead of the full page:

```bash
rodney screenshot-el ".card-grid" /tmp/cards.png
```

This is useful for checking a specific component without the surrounding page chrome.

### Navigating between pages

```bash
rodney open http://localhost:5173/settings    # navigate to a route
rodney open http://localhost:5173/            # back to home
rodney back                                   # browser back button
rodney waitload                               # always wait after navigation
```

### Interacting with the page

For testing dynamic behavior -- clicking buttons, filling forms, toggling state:

```bash
rodney click ".btn-primary"           # click a button
rodney input "input[name=key]" "test" # type into a field
rodney clear "input[name=key]"        # clear a field
rodney select "select.role" "admin"   # select a dropdown value
rodney submit "form"                  # submit a form
rodney hover ".card"                  # hover to test hover styles
```

After any interaction, screenshot to verify the result.

### Evaluating JavaScript

`rodney js` evaluates a single JavaScript expression in the page context and prints the result. It is not a statement runner -- semicolons between statements cause a syntax error.

```bash
# Single expression (correct)
rodney js "document.title"
rodney js "document.querySelectorAll('.card').length"
rodney js "window.innerWidth"

# Multiple statements require an IIFE (correct)
rodney js "(() => { const el = document.querySelector('.badge'); return el ? el.textContent : 'not found'; })()"

# This WILL FAIL -- semicolons between statements are not allowed
# rodney js "const x = 1; console.log(x)"
```

Useful JS checks:

```bash
# Check for console errors (reads the last 50 entries)
rodney js "JSON.stringify(performance.getEntriesByType('resource').filter(e => e.responseStatus >= 400).map(e => e.name))"

# Check computed styles
rodney js "getComputedStyle(document.querySelector('.page-header')).fontSize"

# Check Vue component state (if exposed on the element)
rodney js "document.querySelector('#app').__vue_app__ ? 'vue mounted' : 'no vue'"

# Inject a cookie (useful for auth, see below)
rodney js "document.cookie = 'session_token=TOKEN_HERE; path=/'"
```

### Waiting for things

```bash
rodney waitload                   # wait for the page load event
rodney waitstable                 # wait for the DOM to stop changing (good after dynamic updates)
rodney waitidle                   # wait for network requests to settle
rodney wait ".data-table"         # wait for a specific element to appear in the DOM
```

Use `waitload` after `rodney open` or `rodney reload`. Use `waitstable` after clicking a button that triggers a re-render. Use `wait` when you expect an element to appear asynchronously (e.g., after an API call resolves).

The `wait` command has a default timeout. If the element never appears, it will eventually exit with code 2. Do not use `wait` to poll for elements you are unsure about -- use `exists` instead.

### Accessibility tree

The accessibility tree is one of the most useful inspection tools. It shows the semantic structure of the page as a screen reader would see it:

```bash
rodney ax-tree
```

Output looks like:

```
[RootWebArea] "Page Title" (focusable)
  [heading] "Executions" (level=1)
    [StaticText] "Executions"
  [table]
    [row]
      [cell] [link] "gentle-nocturne" (focusable)
      [cell] [StaticText] "running"
  [link] "Settings" (focusable)
```

To find specific elements by role or name:

```bash
rodney ax-find --role button                 # all buttons
rodney ax-find --name "Settings"             # elements named "Settings"
rodney ax-find --role link --name "Settings" # links named "Settings"
rodney ax-node ".btn-primary"                # a11y info for a specific element
```

### When to screenshot

- After creating or modifying a component
- After changing CSS (global or scoped)
- After adding responsive styles (screenshot at both desktop and 375px width)
- After any layout change
- Before declaring the work done

Do not screenshot after every single line change. Batch your edits, then screenshot to verify. The loop is edit a coherent chunk, reload, screenshot, evaluate.

### Reference screenshots

The `frontend/screenshots/` directory contains reference images for the current design at both desktop and mobile sizes. Compare your screenshots against these to verify you have not broken existing pages. The files follow the pattern `{page}-{desktop|mobile}.png`.

### Troubleshooting

**`rodney` says "failed to connect to browser":** The browser is not running or rodney lost the connection. Re-launch Chromium and reconnect:

```bash
pkill -f "chromium.*remote-debugging" 2>/dev/null
chromium-browser --headless --no-sandbox --disable-gpu --remote-debugging-port=9222 &
rodney connect 127.0.0.1:9222
```

**Page is blank or shows an error:** Check if the Vite dev server is running (`curl -s http://localhost:5173 | head -5`). If not, start it with `cd frontend && npm run dev &`.

**HMR is not picking up changes:** Use `rodney reload --hard` to bypass the cache. If that does not help, restart the Vite dev server.

**Selectors with special characters:** Quote selectors carefully in bash. Use double quotes around the selector and single quotes inside if needed:

```bash
rodney text "div[data-id='123']"
rodney click ".btn:not(.btn-disabled)"
```

**Multiple pages/tabs:** If you accidentally open multiple tabs, list them and switch:

```bash
rodney pages           # list all tabs with index numbers
rodney page 0          # switch to first tab
rodney closepage 1     # close second tab
```

## API proxy

`vite.config.js` proxies `/api` to `http://localhost:8000`. The `api.js` module provides `get(path)`, `post(path, body)`, and `del(path)` that prepend `/api` and handle auth redirects. When adding new API calls, use these helpers rather than raw `fetch`.

## Auth model

The router guard in `router.js` calls `GET /api/me` on first navigation. If it fails, the user is redirected to `/welcome`. Routes with `meta: { public: true }` skip the auth check. The `useAuth` composable in `auth.js` manages user state and is used by `App.vue` to show the sidebar user info.

### How auth works on the server

Auth behavior depends on whether GitHub App env vars are configured (see `config.py:has_github_app`).

**Local dev (no GitHub App):** When `GITHUB_CLIENT_ID`, `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY`, and `GITHUB_APP_SLUG` are not set, the server skips auth entirely. `GET /api/me` returns a stub `local` user. Rodney can navigate all pages freely.

**Production (GitHub OAuth enabled):** The server accepts auth via two mechanisms, checked in this order by `get_caller` in `api/deps.py`:

1. `Authorization: Bearer <token>` header -- either an API key (prefixed `druid_`) or a JWT
2. `session_token` cookie -- a JWT set by the OAuth callback at `/api/oauth/callback`

The browser flow uses the cookie. GitHub OAuth redirects back with a code, the server exchanges it for a GitHub access token, looks up or creates the user, mints a JWT, and sets it as an httpOnly cookie named `session_token`.

### Using rodney when OAuth is enabled

Rodney cannot complete the GitHub OAuth redirect flow (it would need to log in to GitHub). There are two ways to authenticate the browser session:

**Option A: Mint a session JWT (recommended).** This requires the `FORWARDING_TOKEN_SECRET` env var (the server uses it to sign JWTs) and a user ID from the database. Run this from the `server/` directory:

```bash
cd server && uv run python -c "
from druids_server.utils.forwarding_tokens import mint_user_token
from druids_server.db.models.user import User
# Replace with the actual user ID (UUID from the users table)
print(mint_user_token('<user-id>'))
"
```

Then inject the token into rodney:

```bash
rodney js "document.cookie = 'session_token=<token>; path=/'"
rodney reload
```

The JWT is valid for 7 days (`USER_TOKEN_LIFETIME` in `utils/forwarding_tokens.py`).

**Option B: Copy a session cookie from your browser.** If you are already logged in, open DevTools, copy the value of the `session_token` cookie, and inject it:

```bash
rodney js "document.cookie = 'session_token=<copied-value>; path=/'"
rodney reload
```

**Note on API keys:** API keys (`druid_...`) work in the `Authorization` header but not as cookies. The browser uses cookies, so you need a JWT for rodney. There is currently no endpoint that exchanges an API key for a session cookie.

For most frontend development, the simplest approach is to run the server without the GitHub App env vars so auth is bypassed entirely.

## Adding a new page

1. Create `frontend/src/pages/YourPage.vue` following the `<script setup>` pattern
2. Add a route in `router.js`
3. Add a sidebar link in `App.vue` if the page should be navigable
4. Use the existing CSS classes from `style.css` for layout and styling
5. Include both a desktop table layout and a `.mobile-cards` layout if the page shows tabular data
6. Screenshot at desktop and mobile widths to verify
