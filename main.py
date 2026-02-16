#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import mimetypes
import os
import re
import socketserver
import threading
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qs, urlencode, urlparse


@dataclass
class OrgFile:
    path: Path
    relative_path: str
    title: str
    file_id: str | None
    content: str


ROOT_DIR = Path(__file__).resolve().parent
TEMPLATE_PATH = ROOT_DIR / "templates" / "index.html"
STATIC_DIR = ROOT_DIR / "static"
TITLE_RE = re.compile(r"^\s*#\+TITLE:\s*(.+?)\s*$", flags=re.IGNORECASE)
ID_RE = re.compile(r"^\s*:ID:\s*(\S+)\s*$", flags=re.IGNORECASE)
ORG_LINK_RE = re.compile(r"\[\[([^\]]+)\](?:\[([^\]]*)\])?\]")
URL_RE = re.compile(r"https?://[^\s<>'\"()\[\]]+")
CREATED_LINE_RE = re.compile(r"^\s*(?:#\+)?CREATED:\s*(.+?)\s*$", flags=re.IGNORECASE)
ORG_TIMESTAMP_RE = re.compile(r"[\[<](\d{4})-(\d{2})-(\d{2})(?:\s+\w{3})?(?:\s+(\d{2}):(\d{2}))?[\]>]")
DEFAULT_CONFIG_PATH = ROOT_DIR / "config.yaml"


def scan_org_files(base_dir: Path) -> list[OrgFile]:
    """Recursively find .org files under base_dir and return their contents."""
    org_files: list[OrgFile] = []

    for root, dirs, files in os.walk(base_dir):
        dirs[:] = [d for d in dirs if d not in {".git", ".venv", "venv", "__pycache__"}]
        root_path = Path(root)
        for filename in files:
            if filename.lower().endswith(".org"):
                file_path = root_path / filename
                try:
                    content = file_path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    content = file_path.read_text(encoding="utf-8", errors="replace")

                org_files.append(
                    OrgFile(
                        path=file_path,
                        relative_path=normalize_relative_path(str(file_path.relative_to(base_dir))),
                        title=extract_org_title(content, file_path.stem),
                        file_id=extract_org_id(content),
                        content=content,
                    )
                )

    org_files.sort(key=lambda f: f.relative_path.lower())
    return org_files


def extract_org_title(content: str, fallback: str) -> str:
    for line in content.splitlines():
        match = TITLE_RE.match(line)
        if match:
            return match.group(1).strip()
    return fallback


def extract_org_id(content: str) -> str | None:
    for line in content.splitlines():
        match = ID_RE.match(line)
        if match:
            return match.group(1).strip()
    return None


def normalize_relative_path(path_value: str) -> str:
    normalized = os.path.normpath(path_value.replace("\\", "/"))
    if normalized == ".":
        return ""
    return normalized.lstrip("./")


def extract_org_link_targets(content: str) -> list[str]:
    targets: list[str] = []
    for match in ORG_LINK_RE.finditer(content):
        targets.append(match.group(1).strip())
    return targets


def resolve_link_target(
    source_relative_path: str,
    raw_target: str,
    known_paths: set[str],
    id_to_path: dict[str, str],
) -> str | None:
    target = raw_target.strip()
    if not target:
        return None
    if "::" in target:
        target = target.split("::", 1)[0]
    if "#" in target:
        target = target.split("#", 1)[0]
    if target.lower().startswith("id:"):
        id_value = target[3:].strip().lower()
        return id_to_path.get(id_value)
    if target.startswith("file:"):
        target = target[5:]
    elif "://" in target:
        return None
    elif ":" in target and not target.endswith(".org"):
        return None

    source_dir = os.path.dirname(source_relative_path)
    if target.startswith("/"):
        candidate = normalize_relative_path(target.lstrip("/"))
    else:
        candidate = normalize_relative_path(os.path.join(source_dir, target))

    if not candidate:
        return None
    if candidate in known_paths:
        return candidate
    if not candidate.endswith(".org"):
        with_suffix = f"{candidate}.org"
        if with_suffix in known_paths:
            return with_suffix
    return None


def find_backlinks(org_files: Iterable[OrgFile], selected_path: str) -> list[OrgFile]:
    files = list(org_files)
    known_paths = {f.relative_path for f in files}
    id_to_path = {f.file_id.lower(): f.relative_path for f in files if f.file_id}
    backlinks: list[OrgFile] = []
    seen_sources: set[str] = set()

    for source in files:
        if source.relative_path == selected_path:
            continue
        link_targets = extract_org_link_targets(source.content)
        for raw_target in link_targets:
            resolved = resolve_link_target(source.relative_path, raw_target, known_paths, id_to_path)
            if resolved == selected_path and source.relative_path not in seen_sources:
                backlinks.append(source)
                seen_sources.add(source.relative_path)
                break

    backlinks.sort(key=lambda f: (f.title.lower(), f.relative_path.lower()))
    return backlinks


def build_backlink_counts(org_files: Iterable[OrgFile]) -> dict[str, int]:
    files = list(org_files)
    known_paths = {f.relative_path for f in files}
    id_to_path = {f.file_id.lower(): f.relative_path for f in files if f.file_id}
    counts = {f.relative_path: 0 for f in files}

    for source in files:
        seen_targets: set[str] = set()
        for raw_target in extract_org_link_targets(source.content):
            resolved = resolve_link_target(source.relative_path, raw_target, known_paths, id_to_path)
            if not resolved or resolved == source.relative_path or resolved in seen_targets:
                continue
            if resolved in counts:
                counts[resolved] += 1
                seen_targets.add(resolved)

    return counts


def _timestamp_match_to_sort_key(match: re.Match[str]) -> int | None:
    try:
        year = int(match.group(1))
        month = int(match.group(2))
        day = int(match.group(3))
        hour = int(match.group(4) or "0")
        minute = int(match.group(5) or "0")
    except ValueError:
        return None
    return year * 100000000 + month * 1000000 + day * 10000 + hour * 100 + minute


def extract_created_sort_key(content: str) -> int | None:
    for line in content.splitlines():
        created_match = CREATED_LINE_RE.match(line)
        if created_match:
            ts_match = ORG_TIMESTAMP_RE.search(created_match.group(1))
            if ts_match:
                key = _timestamp_match_to_sort_key(ts_match)
                if key is not None:
                    return key

    first_ts = ORG_TIMESTAMP_RE.search(content)
    if first_ts:
        return _timestamp_match_to_sort_key(first_ts)
    return None


def note_href(relative_path: str, edit_mode: bool) -> str:
    params = {"file": relative_path}
    if edit_mode:
        params["edit"] = "1"
    return "/?" + urlencode(params)


def truncate_label(text: str, max_chars: int = 32) -> str:
    if len(text) <= max_chars:
        return text
    if max_chars <= 3:
        return "." * max_chars
    return text[: max_chars - 3] + "..."


def render_plain_text_with_links(text: str) -> str:
    rendered_parts: list[str] = []
    cursor = 0
    for match in URL_RE.finditer(text):
        start, end = match.span()
        if start > cursor:
            rendered_parts.append(html.escape(text[cursor:start]))
        url = match.group(0)
        safe_url = html.escape(url, quote=True)
        rendered_parts.append(f"<a class='org-link' href='{safe_url}' target='_blank' rel='noopener noreferrer'>{safe_url}</a>")
        cursor = end
    if cursor < len(text):
        rendered_parts.append(html.escape(text[cursor:]))
    return "".join(rendered_parts)


def render_line_with_links(
    line: str, source_relative_path: str, known_paths: set[str], id_to_path: dict[str, str]
) -> str:
    rendered_parts: list[str] = []
    cursor = 0

    for match in ORG_LINK_RE.finditer(line):
        start, end = match.span()
        if start > cursor:
            rendered_parts.append(render_plain_text_with_links(line[cursor:start]))

        raw_target = match.group(1).strip()
        label = (match.group(2) or "").strip()
        resolved = resolve_link_target(source_relative_path, raw_target, known_paths, id_to_path)
        if resolved:
            safe_href = html.escape(note_href(resolved, False), quote=True)
            link_text = label if label else raw_target
            rendered_parts.append(f"<a class='org-link' href='{safe_href}'>{html.escape(link_text)}</a>")
        else:
            rendered_parts.append(render_plain_text_with_links(match.group(0)))

        cursor = end

    if cursor < len(line):
        rendered_parts.append(render_plain_text_with_links(line[cursor:]))

    return "".join(rendered_parts)


def render_org_to_html(
    content: str, source_relative_path: str, known_paths: set[str], id_to_path: dict[str, str]
) -> str:
    """Very small org-ish renderer: headings become section titles, body keeps line breaks."""
    html_lines: list[str] = []

    for raw_line in content.splitlines():
        line = raw_line.rstrip("\n")
        stripped = line.lstrip()

        if stripped.startswith("*"):
            stars = len(stripped) - len(stripped.lstrip("*"))
            if stars > 0 and len(stripped) > stars and stripped[stars] == " ":
                level = min(stars + 1, 6)
                title = html.escape(stripped[stars + 1 :])
                html_lines.append(f"<h{level}>{title}</h{level}>")
                continue

        if not stripped:
            html_lines.append("<div class='spacer'></div>")
        else:
            rendered_line = render_line_with_links(line, source_relative_path, known_paths, id_to_path)
            html_lines.append(f"<p>{rendered_line}</p>")

    return "\n".join(html_lines)


def find_org_file(org_files: Iterable[OrgFile], relative_path: str | None) -> OrgFile | None:
    if relative_path is None:
        return None
    for org_file in org_files:
        if org_file.relative_path == relative_path:
            return org_file
    return None


def build_index_page(
    org_files: Iterable[OrgFile],
    selected_path: str | None = None,
    edit_mode: bool = False,
    status_message: str | None = None,
    status_level: str = "success",
) -> str:
    org_files = list(org_files)

    if not org_files:
        body = "<p>No .org files were found in this directory.</p>"
        nav = ""
        backlinks_html = "<p class='backlinks-empty'>Open a note to see backlinks.</p>"
    else:
        selected = find_org_file(org_files, selected_path) or org_files[0]
        known_paths = {f.relative_path for f in org_files}
        id_to_path = {f.file_id.lower(): f.relative_path for f in org_files if f.file_id}
        backlinks = find_backlinks(org_files, selected.relative_path)
        backlink_counts = build_backlink_counts(org_files)

        nav_items = []
        for f in org_files:
            active = "active" if f.relative_path == selected.relative_path else ""
            safe_href = html.escape(note_href(f.relative_path, False), quote=True)
            safe_title = html.escape(truncate_label(f.title))
            safe_path = html.escape(truncate_label(f.relative_path))
            full_title = html.escape(f.title, quote=True)
            full_path = html.escape(f.relative_path, quote=True)
            searchable = html.escape(f"{f.title} {f.relative_path}".lower(), quote=True)
            backlinks_count = backlink_counts.get(f.relative_path, 0)
            created_key = extract_created_sort_key(f.content)
            created_key_attr = "" if created_key is None else str(created_key)
            nav_items.append(
                f"<a class='file-link {active}' data-search='{searchable}' "
                f"data-backlinks='{backlinks_count}' data-created-ts='{created_key_attr}' href='{safe_href}'>"
                f"<span class='file-title' title='{full_title}'>{safe_title}</span>"
                f"<span class='file-path' title='{full_path}'>{safe_path}</span>"
                "</a>"
            )

        nav = "\n".join(nav_items)
        mode_toggle = (
            f"<a class='mode-link' href='{html.escape(note_href(selected.relative_path, False), quote=True)}'>Preview</a>"
            if edit_mode
            else f"<a class='mode-link' href='{html.escape(note_href(selected.relative_path, True), quote=True)}'>Edit</a>"
        )
        status_html = ""
        if status_message:
            safe_message = html.escape(status_message)
            safe_level = "error" if status_level == "error" else "success"
            status_html = f"<p class='status status-{safe_level}'>{safe_message}</p>"

        if backlinks:
            backlink_items = []
            for source in backlinks:
                safe_href = html.escape(note_href(source.relative_path, edit_mode), quote=True)
                safe_title = html.escape(truncate_label(source.title))
                safe_path = html.escape(truncate_label(source.relative_path))
                full_title = html.escape(source.title, quote=True)
                full_path = html.escape(source.relative_path, quote=True)
                backlink_items.append(
                    f"<a class='backlink-item' href='{safe_href}'>"
                    f"<span class='file-title' title='{full_title}'>{safe_title}</span>"
                    f"<span class='file-path' title='{full_path}'>{safe_path}</span>"
                    "</a>"
                )
            backlinks_html = "\n".join(backlink_items)
        else:
            backlinks_html = "<p class='backlinks-empty'>No notes link to this note yet.</p>"

        if edit_mode:
            body = (
                f"<h2>Editing {html.escape(selected.relative_path)}</h2>"
                f"<div class='toolbar'>{mode_toggle}</div>"
                f"{status_html}"
                "<form class='editor-form' method='post' action='/edit'>"
                f"<input type='hidden' name='file' value='{html.escape(selected.relative_path, quote=True)}'>"
                f"<textarea class='editor-box' name='content'>{html.escape(selected.content)}</textarea>"
                "<button class='submit-btn' type='submit'>Submit</button>"
                "</form>"
            )
        else:
            body = (
                f"<h2>{html.escape(selected.relative_path)}</h2>"
                f"<div class='toolbar'>{mode_toggle}</div>"
                f"{status_html}"
                f"<article class='org-content'>{render_org_to_html(selected.content, selected.relative_path, known_paths, id_to_path)}</article>"
            )

    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    return (
        template.replace("{{NAV_ITEMS}}", nav)
        .replace("{{MAIN_CONTENT}}", body)
        .replace("{{BACKLINKS}}", backlinks_html)
    )


def make_handler(base_dir: Path):
    class OrgRequestHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)

            if parsed.path.startswith("/static/"):
                self.serve_static(parsed.path)
                return

            if parsed.path != "/":
                self.send_error(404, "Not found")
                return

            params = parse_qs(parsed.query)
            selected_path = params.get("file", [None])[0]
            edit_mode = params.get("edit", ["0"])[0] == "1"
            saved = params.get("saved", ["0"])[0] == "1"
            error = params.get("error", [""])[0]
            status_message = None
            status_level = "success"
            if saved:
                status_message = "Saved successfully."
            elif error == "missing":
                status_message = "Select a valid .org file first."
                status_level = "error"
            elif error == "write":
                status_message = "Could not write this file."
                status_level = "error"

            org_files = scan_org_files(base_dir)
            html_page = build_index_page(
                org_files,
                selected_path=selected_path,
                edit_mode=edit_mode,
                status_message=status_message,
                status_level=status_level,
            )
            encoded = html_page.encode("utf-8")

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path != "/edit":
                self.send_error(404, "Not found")
                return

            content_length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(content_length).decode("utf-8", errors="replace")
            params = parse_qs(body)
            selected_path = params.get("file", [None])[0]
            new_content = params.get("content", [""])[0]

            org_files = scan_org_files(base_dir)
            selected = find_org_file(org_files, selected_path)
            if selected is None:
                self.redirect_with_query("/", {"edit": "1", "error": "missing"})
                return

            try:
                selected.path.write_text(new_content, encoding="utf-8")
            except OSError:
                self.redirect_with_query("/", {"file": selected.relative_path, "edit": "1", "error": "write"})
                return

            self.redirect_with_query("/", {"file": selected.relative_path, "edit": "1", "saved": "1"})

        def redirect_with_query(self, path: str, params: dict[str, str]) -> None:
            location = f"{path}?{urlencode(params)}"
            self.send_response(303)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def serve_static(self, request_path: str) -> None:
            rel_path = request_path[len("/static/") :]
            candidate = (STATIC_DIR / rel_path).resolve()
            if STATIC_DIR.resolve() not in candidate.parents and candidate != STATIC_DIR.resolve():
                self.send_error(403, "Forbidden")
                return
            if not candidate.is_file():
                self.send_error(404, "Not found")
                return

            data = candidate.read_bytes()
            content_type = mimetypes.guess_type(str(candidate))[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, fmt: str, *args) -> None:
            return

    return OrgRequestHandler


def serve(base_dir: Path, host: str, port: int, open_browser: bool = True) -> None:
    handler_cls = make_handler(base_dir)

    class ReusableHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    server = ReusableHTTPServer((host, port), handler_cls)
    actual_port = server.server_address[1]
    url = f"http://{host}:{actual_port}/"
    print(f"Serving org files from {base_dir}")
    print(f"Open {url}")

    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        server.server_close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve local .org files in a browser-friendly web view.")
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help=f"Path to config YAML file (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument("--dir", default="notes", help="Directory to scan for .org files (default: notes)")
    parser.add_argument(
        "--host",
        default=None,
        help="Host/IP to bind the web server (overrides config bind_addr)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port to bind (overrides config bind_port)",
    )
    parser.add_argument("--no-browser", action="store_true", help="Don't auto-open the browser")
    return parser.parse_args()


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and ((value[0] == value[-1] == "'") or (value[0] == value[-1] == '"')):
        return value[1:-1]
    return value


def load_runtime_config(config_path: Path) -> dict[str, str | int]:
    config: dict[str, str] = {}
    if config_path.exists():
        text = config_path.read_text(encoding="utf-8")
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip()
            if not key:
                continue
            if "#" in value:
                value = value.split("#", 1)[0].strip()
            config[key] = _strip_quotes(value)

    bind_addr = config.get("bind_addr", "127.0.0.1")
    bind_port_raw = config.get("bind_port", "8000")
    try:
        bind_port = int(bind_port_raw)
    except ValueError as exc:
        raise ValueError(f"Invalid bind_port in {config_path}: {bind_port_raw!r}") from exc
    if not 1 <= bind_port <= 65535:
        raise ValueError(f"bind_port out of range in {config_path}: {bind_port}")

    return {"bind_addr": bind_addr, "bind_port": bind_port}


def main() -> None:
    args = parse_args()
    config = load_runtime_config(Path(args.config))
    base_dir = Path(args.dir).resolve()
    host = args.host if args.host is not None else str(config["bind_addr"])
    port = args.port if args.port is not None else int(config["bind_port"])
    serve(base_dir, host, port, open_browser=not args.no_browser)


if __name__ == "__main__":
    main()
