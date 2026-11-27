"""Command line interface: ``plainonto init|refresh|drift|check|export|validate|rename|mcp``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .api import compare, init, refresh
from .check import check, validate_record
from .export import to_linkml, to_shacl, to_turtle
from .rename import rename
from .textformat import ParseError, load, save


def _cmd_init(args) -> int:
    path = Path(args.file)
    if path.exists() and not args.force:
        print(f"{path} already exists; use 'plainonto refresh' or pass --force", file=sys.stderr)
        return 2
    onto, report = init(path, args.sources)
    props = sum(len(c.properties()) for c in onto.concepts.values())
    print(f"Wrote {path}: {len(onto.concepts)} concepts, {props} properties from {len(onto.sources)} source(s).")
    for warning in report.warnings:
        print(warning.render())
    if report.warnings:
        from .merge import SCOPE_HINT

        print(SCOPE_HINT)
    return 0


def _cmd_refresh(args) -> int:
    report = refresh(Path(args.file), renames=not args.no_renames)
    print(report.render())
    return 0


_SKIP_DIRS = {"node_modules", "__pycache__"}


def _skipped(folder: Path) -> bool:
    """Hidden folders (.git, .venv), node_modules, __pycache__ and virtual environments."""
    return folder.name.startswith(".") or folder.name in _SKIP_DIRS or (folder / "pyvenv.cfg").exists()


def _find_onto_files() -> list[str]:
    """All .onto files under the current folder, except in hidden folders,
    node_modules, __pycache__ and virtual environments."""
    found = []
    for path in sorted(Path().rglob("*.onto")):
        folders = [Path(*path.parts[: i + 1]) for i in range(len(path.parts) - 1)]
        if not any(_skipped(f) for f in folders):
            found.append(path.as_posix())
    return found


def _error(exc: BaseException) -> str:
    if isinstance(exc, (ParseError, ValueError, FileNotFoundError)):
        return f"plainonto: {exc}"
    return f"plainonto: unexpected error ({type(exc).__name__}): {exc}"


def _each(files: list[str], run) -> int:
    """Run ``run(path)`` for every file, with a heading when there are several.
    Returns the worst exit code: 1 for drift or issues, 2 for anything that
    could not be read or failed (never 1, so a failure is never taken for drift)."""
    if not files:
        files = _find_onto_files()
        if not files:
            print("plainonto: no .onto files given or found under the current folder", file=sys.stderr)
            return 2
    worst = 0
    for path in files:
        if len(files) > 1:
            print(f"== {path}")
        try:
            from .extract import within_root

            within_root(Path(path))  # with PLAINONTO_SOURCE_ROOT set, the file must be inside it too
            worst = max(worst, run(Path(path)))
        except Exception as exc:
            print(_error(exc), file=sys.stderr)
            worst = 2
    return worst


def _cmd_drift(args) -> int:
    def one(path: Path) -> int:
        _, _, report = compare(path, renames=not args.no_renames)
        print(report.render())
        return 1 if report.has_drift else 0

    return _each(args.files, one)


def _cmd_check(args) -> int:
    def one(path: Path) -> int:
        issues = check(load(path))
        for issue in issues:
            print(issue.render())
        if not issues:
            print("No issues.")
        errors = [i for i in issues if i.level == "error" or (args.strict and i.level == "warning")]
        return 1 if errors else 0

    return _each(args.files, one)


def _cmd_export(args) -> int:
    onto = load(args.file)
    name = Path(args.file).stem
    text = {
        "turtle": lambda: to_turtle(onto, args.base),
        "shacl": lambda: to_shacl(onto, args.base),
        "linkml": lambda: to_linkml(onto, name, args.base),
    }[args.to]()
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8", newline="\n")
        print(f"Wrote {args.output}")
    else:
        sys.stdout.write(text)
    return 0


def _cmd_rename(args) -> int:
    onto = load(args.file)
    message = rename(onto, args.old, args.new)
    save(args.file, onto)
    print(message)
    return 0


def _cmd_validate(args) -> int:
    raw = sys.stdin.read() if args.record == "-" else Path(args.record).read_text(encoding="utf-8-sig")
    data = json.loads(raw)
    records = data if isinstance(data, list) else [data]
    whole = load(args.file)
    onto = whole.accepted()  # proposals do not count until a person accepts them
    if onto.find(args.concept) is None:
        if whole.find(args.concept) is not None:
            print(f"plainonto: concept {args.concept} is still [proposed]; accept it first", file=sys.stderr)
        else:
            print(f"plainonto: no concept named {args.concept} in {args.file}", file=sys.stderr)
        return 2
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
    s.add_argument(
        "--from",
        dest="sources",
        action="append",
        required=True,
        metavar="KIND:PATH",
        help="sqlite:db-or-.sql, jsonschema:file.json or tools:tools.json (repeatable)",
    )
    s.add_argument("--force", action="store_true", help="overwrite an existing file")
    s.set_defaults(func=_cmd_init)

    no_renames = "never treat a vanished concept or property as renamed; report it as gone and added instead"
    s = sub.add_parser("refresh", help="re-extract from the file's sources; your lines are kept")
    s.add_argument("file")
    s.add_argument("--no-renames", action="store_true", help=no_renames)
    s.set_defaults(func=_cmd_refresh)

    s = sub.add_parser("drift", help="report what changed in the sources without writing (exit 1 on drift)")
    s.add_argument("files", nargs="*", metavar="file", help="default: every .onto file under the current folder")
    s.add_argument("--no-renames", action="store_true", help=no_renames)
    s.set_defaults(func=_cmd_drift)

    s = sub.add_parser(
        "check", help="check files for duplicates, unknown targets, misplaced rules and pending proposals"
    )
    s.add_argument("files", nargs="*", metavar="file", help="default: every .onto file under the current folder")
    s.add_argument("--strict", action="store_true", help="fail on warnings too")
    s.set_defaults(func=_cmd_check)

    s = sub.add_parser("export", help="export to Turtle (OWL + SKOS), SHACL shapes or LinkML")
    s.add_argument("file")
    s.add_argument("--to", choices=["turtle", "shacl", "linkml"], required=True)
    s.add_argument("-o", "--output")
    s.add_argument(
        "--base", help="base IRI (default: the file's 'prefix :' line, else https://example.org/plainontology#)"
    )
    s.set_defaults(func=_cmd_export)

    s = sub.add_parser("validate", help="check JSON records against a concept (exit 1 on problems)")
    s.add_argument("file")
    s.add_argument("concept")
    s.add_argument("record", help="JSON file with one object or a list of objects, or - for stdin")
    s.set_defaults(func=_cmd_validate)

    s = sub.add_parser("rename", help="rename a concept or Concept.property everywhere in the file")
    s.add_argument("file")
    s.add_argument("old", help="Concept or Concept.property")
    s.add_argument("new", help="new name; renaming onto an existing name merges the two")
    s.set_defaults(func=_cmd_rename)

    s = sub.add_parser("mcp", help="serve the file to AI agents over MCP (stdio)")
    s.add_argument("file")
    s.add_argument(
        "--allow-proposals", action="store_true", help="let agents add [proposed] lines for a person to review"
    )
    s.set_defaults(func=_cmd_mcp)
    return p


def _utf8_output() -> None:
    """Write UTF-8 when output goes to a file or pipe (on Windows the default
    code page cannot show names such as 日本), and never crash on a character."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            if stream.isatty():
                reconfigure(errors="replace")
            else:
                reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    _utf8_output()
    args = build_parser().parse_args(argv)
    try:
        from .extract import within_root

        for attr in ("file", "record"):  # with PLAINONTO_SOURCE_ROOT set, every file must be inside it
            value = getattr(args, attr, None)
            if isinstance(value, str) and value != "-":
                within_root(Path(value))
        return args.func(args)
    except Exception as exc:
        print(_error(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
