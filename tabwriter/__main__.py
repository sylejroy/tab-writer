"""Command line.

    python -m tabwriter [file.tab]                 open the editor window
    python -m tabwriter build file.tab [-o out]    render to .html or .pdf without opening a window
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .export import ExportError, build


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] != ["build"]:
        ap = argparse.ArgumentParser(prog="tabwriter", description="Open the Tab Writer editor.")
        ap.add_argument("file", nargs="?", help=".tab file to open (created when you save if missing)")
        args = ap.parse_args(argv)
        from .gui import main as gui_main
        gui_main(args.file)
        return 0

    ap = argparse.ArgumentParser(prog="tabwriter build", description="Render a .tab file to .html or .pdf.")
    ap.add_argument("file")
    ap.add_argument("-o", "--output", help="output path; a .pdf extension exports PDF (default: <file>.html)")
    ap.add_argument("--pdf", action="store_true", help="shortcut for -o <file>.pdf")
    args = ap.parse_args(argv[1:])
    out = args.output or (str(Path(args.file).with_suffix(".pdf")) if args.pdf else None)
    try:
        path, song = build(args.file, out)
    except ExportError as exc:
        print(exc, file=sys.stderr)
        return 2
    for p in song.problems:
        print(f"{args.file}:{p.line}: {p.level}: {p.message}", file=sys.stderr)
    print(f"Wrote {path}")
    return 1 if song.errors else 0


if __name__ == "__main__":
    sys.exit(main())
