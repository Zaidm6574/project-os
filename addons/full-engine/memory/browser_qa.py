#!/usr/bin/env python3
"""Lightweight HTML artifact QA (stdlib only, no Playwright required).

For HTML files, checks local href/src references resolve to existing files.
Full Playwright screenshot QA is optional future work (--with-playwright stub).

Usage:
  python3 memory/browser_qa.py <html_file_or_dir> [--root DIR]
  python3 memory/browser_qa.py --selftest

Prints QA: PASS or QA: FAIL.
Standard library only. No network access.
"""
from __future__ import annotations

import argparse
import html.parser
import os
import sys
import tempfile

def _safe_path(path: str) -> str:
    return os.path.abspath(path)


class _LinkParser(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.refs: list[tuple[str, str]] = []

    def handle_starttag(self, tag, attrs):
        attr_map = dict(attrs)
        for key in ("href", "src"):
            val = attr_map.get(key)
            if val:
                self.refs.append((tag, val))


def _collect_html_files(target: str) -> list[str]:
    if os.path.isfile(target) and target.lower().endswith((".html", ".htm")):
        return [target]
    out = []
    for root, _dirs, files in os.walk(target):
        for name in files:
            if name.lower().endswith((".html", ".htm")):
                out.append(os.path.join(root, name))
    return out


def _is_local_ref(ref: str) -> bool:
    if not ref or ref.startswith(("#", "data:", "javascript:", "mailto:", "http://", "https://")):
        return False
    return True


def check(target: str, root: str | None = None) -> int:
    full = _safe_path(target)
    root = _safe_path(root or (full if os.path.isdir(full) else os.path.dirname(full)))
    html_files = _collect_html_files(full)
    if not html_files:
        print("QA: FAIL (no HTML files found)")
        return 1
    broken = []
    for html_path in html_files:
        base = os.path.dirname(html_path)
        with open(html_path, encoding="utf-8", errors="replace") as fh:
            parser = _LinkParser()
            parser.feed(fh.read())
        for tag, ref in parser.refs:
            if not _is_local_ref(ref):
                continue
            resolved = os.path.normpath(os.path.join(base, ref.split("?")[0].split("#")[0]))
            if not os.path.exists(resolved):
                broken.append((html_path, tag, ref, resolved))
    if broken:
        for html_path, tag, ref, resolved in broken[:5]:
            print("  broken %s %s=%r -> %s" % (os.path.basename(html_path), tag, ref, resolved))
        if len(broken) > 5:
            print("  ... and %d more" % (len(broken) - 5))
        print("QA: FAIL (%d broken local ref(s))" % len(broken))
        return 1
    print("QA: PASS (%d HTML file(s) checked)" % len(html_files))
    return 0


def selftest() -> int:
    with tempfile.TemporaryDirectory(prefix="browser-qa-selftest-") as tmp:
        good = os.path.join(tmp, "index.html")
        with open(good, "w", encoding="utf-8") as fh:
            fh.write('<html><body><a href="ok.txt">x</a><img src="ok.txt"></body></html>')
        with open(os.path.join(tmp, "ok.txt"), "w") as fh:
            fh.write("ok")
        assert check(tmp) == 0
        bad = os.path.join(tmp, "bad.html")
        with open(bad, "w", encoding="utf-8") as fh:
            fh.write('<html><body><a href="missing.html">x</a></body></html>')
        assert check(bad) == 1
    print("browser_qa selftest: OK")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Lightweight HTML link QA.")
    ap.add_argument("target", nargs="?", help="HTML file or directory")
    ap.add_argument("--root", default=None, help="base dir for resolving refs")
    ap.add_argument(
        "--with-playwright",
        action="store_true",
        help="stub: full screenshot QA not implemented (stdlib-only default)",
    )
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.with_playwright:
        print("browser_qa: --with-playwright is a future stub; running stdlib checks only")
    if args.selftest:
        sys.exit(selftest())
    if not args.target:
        ap.error("target is required (or use --selftest)")
    sys.exit(check(args.target, root=args.root))


if __name__ == "__main__":
    main()
