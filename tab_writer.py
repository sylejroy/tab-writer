"""Launch the Tab Writer editor:  python tab_writer.py [song.tab]"""
import sys

from tabwriter.gui import main

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
