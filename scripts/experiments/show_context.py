from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.shared_context.renderer import render_shared_context
from src.shared_context.storage import get_notes


def main() -> None:
    args = parse_args()
    notes = get_notes(Path(args.db), since_seq=args.since)
    print(render_shared_context(notes, mode=args.mode))
    print()
    print("Note type counts")
    counts = Counter(note.type for note in notes)
    for note_type, count in sorted(counts.items()):
        print(f"{note_type}: {count}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Show Shared Context notes.")
    parser.add_argument("--db", default="runs/single_codex_worker/context.sqlite")
    parser.add_argument("--since", type=int, default=0)
    parser.add_argument("--mode", choices=["full", "worker"], default="full")
    return parser.parse_args()


if __name__ == "__main__":
    main()
