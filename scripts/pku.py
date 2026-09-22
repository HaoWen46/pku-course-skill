#!/usr/bin/env python3
"""Search PKU offerings and write course details. No cache or inferred curricula."""
from __future__ import annotations

import argparse
import json
import sys


class ArgumentsError(Exception):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ArgumentsError(message)


def arguments(argv=None):
    parser = Parser(description=__doc__, allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True, parser_class=Parser)
    options = commands.add_parser("options", help="List supported source filter values.", allow_abbrev=False)
    options.add_argument("--field", choices=("terms", "departments"), default="terms")
    for name in ("search", "export"):
        sub = commands.add_parser(name, allow_abbrev=False)
        sub.add_argument("--term", required=True)
        sub.add_argument("--department", default="0")
        sub.add_argument("--query", default="")
        sub.add_argument("--teacher", default="")
        if name == "search":
            sub.add_argument("--offset", type=int, default=0)
            sub.add_argument("--limit", type=int, default=10)
        else:
            sub.add_argument("--out", required=True)
    options.add_argument("--offset", type=int, default=0)
    options.add_argument("--limit", type=int, default=10)
    get = commands.add_parser("get", allow_abbrev=False)
    get.add_argument("ref")
    get.add_argument("--out", required=True)
    return parser.parse_args(argv)


def main(argv=None, client=None) -> int:
    try:
        args = arguments(argv)
    except ArgumentsError as exc:
        print(json.dumps({"error": {"code": "invalid_arguments", "message": str(exc)[:512]}}), file=sys.stderr)
        return 2
    try:
        import dean
        import output
    except ModuleNotFoundError:
        print('{"error":{"code":"dependency_missing","message":"Install requirements.txt with this Python interpreter."}}', file=sys.stderr)
        return 2
    try:
        client = client or dean.Client()
        if args.command in {"search", "options"} and (not 0 <= args.offset <= dean.MAX_ROWS or not 1 <= args.limit <= dean.PAGE_SIZE):
            raise dean.Error("invalid_pagination", "Use offset 0..100000 and limit 1..10.")
        if args.command in {"get", "export"}:
            out = output.destination(args.out)
        code = 0
        if args.command == "options":
            choices = [{"value": c["value"], "label": c["label"]} for c in client.options()[args.field]]
            result = output.page(choices[args.offset:args.offset + args.limit], args.offset, len(choices), field=args.field)
        elif args.command == "search":
            filters = client.filters(args.term, args.department, args.query, args.teacher)
            start = args.offset // dean.PAGE_SIZE * dean.PAGE_SIZE
            total, rows = client.page(args.term, filters, start)
            rows = rows[args.offset - start:]
            if len(rows) < args.limit and start + dean.PAGE_SIZE < total:
                next_total, more = client.page(args.term, filters, start + dean.PAGE_SIZE)
                if next_total != total:
                    raise dean.Error("source_changed", "Catalog total changed during search.")
                rows.extend(more)
            identities = [dean.ref(args.term, r) for r in rows]
            if len(identities) != len(set(identities)):
                raise dean.Error("source_changed", "Search pages repeated an offering.")
            items = [{"ref": dean.ref(args.term, r), "name": dean.text(r["kcmc"]), "teachers": dean.text(r["teacher"]) or None} for r in rows[:args.limit]]
            result = output.page(items, args.offset, total, term=args.term)
        elif args.command == "get":
            term, row = client.resolve(args.ref)
            course = client.detail(term, row)
            output.write_json(out, course)
            result = {"file": str(out), "term": term, "retrieval_status": course["retrieval_status"], "syllabus_status": course["syllabus_status"]}
            if "error" in course:
                result["error"] = course["error"]
                code = 1
        else:
            filters = client.filters(args.term, args.department, args.query, args.teacher)
            result, code = output.export(client, args.term, filters, out)
        serialized = dean.compact(result) + "\n"
        if len(serialized.encode("utf-8")) > output.MAX_STDOUT:
            raise dean.Error("output_too_large", "Command response exceeded the 4 KiB output budget.")
        sys.stdout.write(serialized)
        return code
    except dean.Error as exc:
        error = exc.data()
    except (OSError, ValueError, RuntimeError):
        error = {"code": "io_error", "message": "Output path is unusable; parents must exist, with no symlinks or overwritten files. Inspect retained files."}
    except KeyboardInterrupt:
        print('{"error":{"code":"interrupted","message":"Command interrupted."}}', file=sys.stderr)
        return 130
    print(dean.compact({"error": error}), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
