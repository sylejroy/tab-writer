"""Export helpers: HTML is always available; PDF uses a locally installed Chrome, Edge or Chromium."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .parser import parse
from .render import render_html


class ExportError(RuntimeError):
    pass


def _candidates() -> list[str]:
    names = ["chrome", "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
             "msedge", "microsoft-edge", "microsoft-edge-stable"]
    found = [p for p in (shutil.which(n) for n in names) if p]
    if sys.platform == "win32":
        roots = [os.environ.get(k) for k in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
        for root in filter(None, roots):
            found += [os.path.join(root, r"Google\Chrome\Application\chrome.exe"),
                      os.path.join(root, r"Microsoft\Edge\Application\msedge.exe"),
                      os.path.join(root, r"Chromium\Application\chrome.exe")]
    elif sys.platform == "darwin":
        found += ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                  "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                  "/Applications/Chromium.app/Contents/MacOS/Chromium"]
    return found


def find_browser() -> str | None:
    override = os.environ.get("TABWRITER_BROWSER")
    if override:
        return override
    return next((p for p in _candidates() if os.path.isfile(p)), None)


def html_to_pdf(html: str, out_path: str | os.PathLike) -> Path:
    browser = find_browser()
    if not browser:
        raise ExportError("No Chrome, Edge or Chromium found for PDF export. Export HTML and use the browser's "
                          "Print > Save as PDF, or set TABWRITER_BROWSER to a Chromium-based browser.")
    out = Path(out_path).resolve()
    with tempfile.TemporaryDirectory(prefix="tabwriter-") as tmp:
        src = Path(tmp, "tab.html")
        src.write_text(html, encoding="utf-8")
        cmd = [browser, "--headless", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
               f"--user-data-dir={Path(tmp, 'profile')}", "--no-pdf-header-footer", "--print-to-pdf-no-header",
               f"--print-to-pdf={out}", src.as_uri()]
        if out.exists():
            out.unlink()
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired as exc:
            raise ExportError("The browser took too long to produce the PDF.") from exc
        if not out.exists() or out.stat().st_size == 0:
            detail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
            raise ExportError("PDF export failed. " + " ".join(detail))
    return out


def build(source_path: str | os.PathLike, out_path: str | os.PathLike | None = None):
    """Render a .tab file to .html or .pdf (chosen by the output extension). Returns (path, song)."""
    src = Path(source_path)
    song = parse(src.read_text(encoding="utf-8"))
    out = Path(out_path) if out_path else src.with_suffix(".html")
    html = render_html(song)
    if out.suffix.lower() == ".pdf":
        html_to_pdf(html, out)
    else:
        out.write_text(html, encoding="utf-8")
    return out, song
