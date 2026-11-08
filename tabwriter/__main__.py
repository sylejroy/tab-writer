"""Command line.

    python -m tabwriter [file.tab]                 open the editor window
    python -m tabwriter build file.tab [-o out]    render to .html or .pdf without opening a window
    python -m tabwriter import VIDEO [-o out.tab]  turn a guitar-transcriber analysis into a .tab file
    python -m tabwriter import --list              show the songs guitar-transcriber has analysed
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .export import ExportError, build


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["import"]:
        return import_main(argv[1:])
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


def import_main(argv: list[str]) -> int:
    from .serialize import serialize
    from .transcriber import Transcriber, TranscriberError, song_from_analysis

    ap = argparse.ArgumentParser(prog="tabwriter import",
                                 description="Turn a guitar-transcriber analysis into an editable .tab file.")
    ap.add_argument("video", nargs="?", help="YouTube video id, song folder name, or path to a song folder")
    ap.add_argument("-o", "--output", help="where to write the .tab (default: <title>.tab)")
    ap.add_argument("--list", action="store_true", help="list analysed songs")
    ap.add_argument("--no-capo", action="store_true", help="use plain shapes even if a capo was found")
    ap.add_argument("--no-arrows", action="store_true", help="leave out strum arrows")
    ap.add_argument("--sixteenths", action="store_true", help="keep sixteenth-note rhythm instead of eighths")
    ap.add_argument("--transcriber", help="guitar-transcriber folder (default: D:/guitar-transcriber)")
    args = ap.parse_args(argv)

    folder = Path(args.video) if args.video and Path(args.video).is_dir() else None
    tr = Transcriber.find(args.transcriber)
    if folder is None and tr is None:
        print("guitar-transcriber not found; pass --transcriber <folder>", file=sys.stderr)
        return 2
    if args.list or not args.video:
        for e in tr.library():
            print(f"{e.get('id') or e['slug']:13s}  {e.get('title', '')}")
        return 0
    if folder is None:
        slug = tr.slug_for(args.video) or args.video
        folder = tr.song_dir(slug)
    try:
        song = song_from_analysis(folder, use_capo=not args.no_capo, arrows=not args.no_arrows,
                                  rhythm=16 if args.sixteenths else 8)
    except TranscriberError as exc:
        print(exc, file=sys.stderr)
        return 1
    name = "".join(ch for ch in song.title if ch.isalnum() or ch in " -_'").strip() or "song"
    out = Path(args.output or f"{name}.tab")
    out.write_text(serialize(song), encoding="utf-8")
    print(f"Wrote {out} ({len(list(song.measures()))} bars{f', capo {song.capo}' if song.capo else ''})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
