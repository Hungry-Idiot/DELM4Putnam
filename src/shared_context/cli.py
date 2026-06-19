from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from src.shared_context.renderer import render_notes
from src.shared_context.schema import SharedNote
from src.shared_context.storage import append_note, get_latest_seq, get_notes, init_db


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "init":
        init_db(Path(args.db))
        print(f"initialized db: {args.db}")
        return 0
    if args.command == "note":
        return command_note(args)
    if args.command == "pull":
        return command_pull(args)
    if args.command == "latest":
        print(get_latest_seq(Path(args.db)))
        return 0
    if args.command == "wait":
        return command_wait(args)

    raise ValueError(f"Unknown command: {args.command}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SQLite shared context CLI.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Initialize a context DB.")
    init_parser.add_argument("--db", required=True)

    note_parser = subparsers.add_parser("note", help="Append one note.")
    note_parser.add_argument("--db", required=True)
    note_parser.add_argument("--problem-id", required=True)
    note_parser.add_argument("--worker", required=True)
    note_parser.add_argument("--type", required=True)
    note_parser.add_argument("--content", required=True)
    note_parser.add_argument("--role", default="agent")
    note_parser.add_argument("--target-seq", type=int)
    note_parser.add_argument("--attempt-path")
    note_parser.add_argument("--metadata-json")

    pull_parser = subparsers.add_parser("pull", help="Read notes.")
    pull_parser.add_argument("--db", required=True)
    pull_parser.add_argument("--since", type=int, default=0)
    pull_parser.add_argument("--problem-id")
    pull_parser.add_argument("--format", choices=["text", "json"], default="text")

    latest_parser = subparsers.add_parser("latest", help="Print latest seq.")
    latest_parser.add_argument("--db", required=True)

    wait_parser = subparsers.add_parser("wait", help="Wait for notes after seq.")
    wait_parser.add_argument("--db", required=True)
    wait_parser.add_argument("--since", type=int, required=True)
    wait_parser.add_argument("--timeout", type=float, default=30)
    wait_parser.add_argument("--poll-interval", type=float, default=1.0)

    return parser.parse_args(argv)


def command_note(args: argparse.Namespace) -> int:
    note = SharedNote(
        seq=None,
        problem_id=args.problem_id,
        worker_id=args.worker,
        type=args.type,
        content=args.content,
        target_seq=args.target_seq,
        attempt_path=args.attempt_path,
        metadata=parse_metadata(args.metadata_json),
    )
    saved = append_note(Path(args.db), note, writer_role=args.role)
    print(json.dumps(note_to_dict(saved), ensure_ascii=False, indent=2))
    return 0


def command_pull(args: argparse.Namespace) -> int:
    notes = get_notes(
        Path(args.db), since_seq=args.since, problem_id=args.problem_id
    )
    print_notes(notes, output_format=args.format)
    return 0


def command_wait(args: argparse.Namespace) -> int:
    db_path = Path(args.db)
    deadline = time.monotonic() + args.timeout
    while time.monotonic() <= deadline:
        notes = get_notes(db_path, since_seq=args.since)
        if notes:
            print_notes(notes, output_format="text")
            return 0
        time.sleep(args.poll_interval)

    print(f"no update after {args.timeout:g}s since seq {args.since}")
    return 0


def print_notes(notes: list[SharedNote], output_format: str) -> None:
    if output_format == "json":
        print(json.dumps([note_to_dict(note) for note in notes], ensure_ascii=False, indent=2))
    else:
        rendered = render_notes(notes)
        if rendered:
            print(rendered)


def parse_metadata(metadata_json: str | None) -> dict[str, Any]:
    if not metadata_json:
        return {}
    metadata = json.loads(metadata_json)
    if not isinstance(metadata, dict):
        raise ValueError("--metadata-json must decode to a JSON object.")
    return metadata


def note_to_dict(note: SharedNote) -> dict[str, Any]:
    return asdict(note)


if __name__ == "__main__":
    raise SystemExit(main())
