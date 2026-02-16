# Org Web Adapter

A lightweight local web app for browsing and editing Org files.

The app is implemented as a single Python server (`main.py`) plus one HTML template (`templates/index.html`) and one stylesheet (`static/style.css`). It scans a notes directory for `.org` files and renders a 3-pane UI:

- left sidebar: note list + search/sort controls
- center pane: note content (preview or edit)
- right sidebar: backlinks to the current note

## Architecture

### High-level flow

1. `main.py` starts an HTTP server.
2. On each page request (`GET /`), it rescans the notes directory for `.org` files.
3. It resolves links/backlinks and builds HTML fragments.
4. It injects those fragments into `templates/index.html` placeholders:
   - `{{NAV_ITEMS}}`
   - `{{MAIN_CONTENT}}`
   - `{{BACKLINKS}}`
5. Browser JS in `templates/index.html` handles client-side interactions (search, shuffle, sorting, jump-to-current, theme toggle).

### Server-side components (`main.py`)

- File discovery and parsing:
  - `scan_org_files(...)` recursively finds `.org` files.
  - Extracts title from `#+TITLE:` and ID from `:ID:`.
- Org link/backlink handling:
  - `resolve_link_target(...)` supports `file:...` and `id:...` links.
  - `find_backlinks(...)` computes notes linking to the selected note.
  - `build_backlink_counts(...)` computes backlink totals for sorting.
- Rendering:
  - `render_org_to_html(...)` converts headings (`*`, `**`, ...) and paragraphs to simple HTML.
  - `render_line_with_links(...)` converts org links in text to clickable app links where resolvable.
  - `truncate_label(...)` caps sidebar labels to 32 chars with `...`.
- Editing:
  - `POST /edit` updates a selected `.org` file and redirects back with status flags.
- Static files:
  - `serve_static(...)` serves files under `static/` with path traversal protection.

### Frontend components

- `templates/index.html`:
  - Base layout markup.
  - Sidebar controls.
  - Small JS controller for filtering/sorting/shuffling nav links.
  - MathJax initialization for inline `$...$` rendering.
- `static/style.css`:
  - 3-column desktop grid and stacked mobile layout.
  - Independent scroll regions for note list and backlinks.
  - Mobile note-list cap (about 5 notes visible before scrolling).

## Configuration

Startup config is read from `config.yaml` by default.

Current config:

```yaml
bind_addr: 10.54.0.3
bind_port: 8001
```

Supported keys:

- `bind_addr`: host/IP to bind
- `bind_port`: TCP port (must be `1..65535`)

Notes:

- If `config.yaml` is missing, defaults are `127.0.0.1:8000`.
- CLI flags `--host` and `--port` override config values.
- You can choose a different config file with `--config /path/to/config.yaml`.

## Running

Basic run:

```bash
python3 main.py
```

Useful options:

```bash
python3 main.py --dir notes
python3 main.py --host 127.0.0.1 --port 9000
python3 main.py --config ./config.yaml
python3 main.py --no-browser
```

## Features

### Note browsing

- Recursive `.org` file discovery.
- Sidebar note list with active-note highlighting.
- Title/path search filter.

### Sidebar ordering controls

- Shuffle notes.
- Sort by backlink count (descending).
- Sort by created date (ascending).
  - Notes without timestamps are treated as older than notes with timestamps.
- Jump to current note button.

### Backlinks

- Right sidebar lists notes linking to the current note.
- Supports both `file:` and `id:` link resolution.

### Editing

- Toggle Preview/Edit for the selected note.
- Save changes from browser to disk.
- Inline status messages for save success/errors.

### Math rendering

- Inline math rendering via MathJax using `$...$` delimiters.

### UI behavior

- Light/dark theme toggle (persisted in `localStorage`).
- Desktop: independent scrollable sidebars.
- Mobile: notes list capped with its own scroll area.
- Sidebar text truncation with ellipsis and tooltip for full text.

## Project layout

- `main.py`: server, parsing, rendering, routing.
- `templates/index.html`: page template + UI behavior JS.
- `static/style.css`: styling and responsive layout.
- `config.yaml`: bind config.
- `notes/`: notes directory (can be a symlink).
- `old_notes/`: alternate local notes snapshot.

## Limitations

- Not a full Org parser; rendering is intentionally simple.
- Notes are rescanned on each request (simple and fresh, but not optimized for huge note sets).
- Math rendering depends on loading MathJax from CDN.

codex resume 019c650e-61d5-73c0-82b6-9872fba7c71e
