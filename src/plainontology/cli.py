"""Command line interface: ``plainonto init|refresh|drift|check|export|validate|mcp``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .api import compare, init, refresh
from .check import check, validate_record
from .export import to_linkml, to_turtle
from .textformat import ParseError, load


def _cmd_init(args) -> int:
    path = Path(args.file)
    if path.exists() and not args.force:
        print(f"{path} already exists; use 'plainonto refresh' or pass --force", file=sys.stderr)
        return 2
    onto, _ = init(path, args.sources)
    props = sum(len(c.properties()) for c in onto.concepts.values())
    print(f"Wrote {path}: {len(onto.concepts)} concepts, {props} properties from {len(onto.sources)} source(s).")
    return 0


def _cmd_refresh(args) -> int:
    report = refresh(Path(args.file))
    print(report.render())
    return 0


def _cmd_drift(args) -> int:
    _, _, report = compare(Path(args.file))
    print(report.render())
    return 1 if report.has_drift else 0


def _cmd_check(args) -> int:
    issues = check(load(args.file))
    for issue in issues:
        print(issue.render())
    errors = [i for i in issues if i.level == "error" or (args.strict and i.level == "warning")]
    if not issues:
        print("No issues.")
    return 1 if errors else 0


def _cmd_export(args) -> int:
    onto = load(args.file)
    name = Path(args.file).stem
    text = to_turtle(onto, args.base) if args.to == "turtle" else to_linkml(onto, name, args.base)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8", newline="\n")
        print(f"Wrote {args.output}")
    else:
        sys.stdout.write(text)
    return 0


def _cmd_validate(args) -> int:
    raw = sys.stdin.read() if args.record == "-" else Path(args.record).read_text(encoding="utf-8")
    data = json.loads(raw)
    records = data if isinstance(data, list) else [data]
    onto = load(args.file)
    failed = 0
    for i, record in enumerate(records):
        issues = validate_record(onto, args.concept, record)
        label = f"record {i}" if len(records) > 1 else "record"
        if issues:
            failed += 1
            print(f"{label}: {len(issues)} problem(s)")
            for issue in issues:
                print(f"  {issue}")
        else:
            print(f"{label}: ok")
    return 1 if failed else 0


def _cmd_mcp(args) -> int:
    try:
        from .mcp_server import build_server
    except ImportError:
        print("The MCP server needs the 'mcp' package: pip install 'plainontology[mcp]'", file=sys.stderr)
        return 2
    build_server(Path(args.file), allow_proposals=args.allow_proposals).run()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="plainonto",
        description="Write down the ontology and semantic layer a system already has, as one plain text file.",
    )
    p.add_argument("--version", action="version", version=f"plainontology {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("init", help="create a file from one or more sources")
    s.add_argument("file")
    s.add_argument("--from", dest="sources", action="append", required=True, metavar="KIND:PATH",
                   help="sqlite:db-or-.sql, jsonschema:file.json or tools:tools.json (repeatable)")
    s.add_argument("--force", action="store_true", help="overwrite an existing file")
    s.set_defaults(func=_cmd_init)

    s = sub.add_parser("refresh", help="re-extract from the file's sources; your lines are kept")
    s.add_argument("file")
    s.set_defaults(func=_cmd_refresh)

    s = sub.add_parser("drift", help="report what changed in the sources without writing (exit 1 on drift)")
    s.add_argument("file")
    s.set_defaults(func=_cmd_drift)

    s = sub.add_parser("check", help="check the file for duplicates, unknown targets and pending proposals")
    s.add_argument("file")
    s.add_argument("--strict", action="store_true", help="fail on warnings too")
    s.set_defaults(func=_cmd_check)

    s = sub.add_parser("export", help="export to Turtle (OWL + SKOS) or LinkML")
    s.add_argument("file")
    s.add_argument("--to", choices=["turtle", "linkml"], required=True)
    s.add_argument("-o", "--output")
    s.add_argument("--base", help="base IRI (default: the file's 'prefix :' line, else https://example.org/plainontology#)")
    s.set_defaults(func=_cmd_export)

    s = sub.add_parser("validate", help="check JSON records against a concept (exit 1 on problems)")
    s.add_argument("file")
    s.add_argument("concept")
    s.add_argument("record", help="JSON file with one object or a list of objects, or - for stdin")
    s.set_defaults(func=_cmd_validate)

    s = sub.add_parser("mcp", help="serve the file to AI agents over MCP (stdio)")
    s.add_argument("file")
    s.add_argument("--allow-proposals", action="store_true",
                   help="let agents add [proposed] lines for a person to review")
    s.set_defaults(func=_cmd_mcp)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ParseError, ValueError, FileNotFoundError) as exc:
        print(f"plainonto: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
