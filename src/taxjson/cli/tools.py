"""The Tools commands: redact, help."""
import argparse
from typing import List

from taxjson.pipeline.messages import _details


def cmd_redact(args: argparse.Namespace) -> None:
    """`tjs redact FILE...`: strip account numbers and identity from
    broker exports (row shapes kept); with no FILE, copy the project's
    inputs/ to inputs_redact/, redacted — see taxjson_redact."""
    from taxjson.bin.taxjson_redact import main as redact_main
    argv: List[str] = [f"--dir={args.dir}"]
    if args.out:
        argv += ["--out", args.out]
    for a in args.also or []:
        argv += ["--also", a]
    if args.no_denylist:
        argv.append("--no-denylist")
    if args.force:
        argv.append("--force")
    if args.check:
        argv.append("--check")
    if _details(args):
        argv.append("--details")
    argv += ["--", *args.files]          # a file named `--check` stays a file
    raise SystemExit(redact_main(argv))


def register(sub: argparse._SubParsersAction) -> None:
    """This group's command, for the `tjs` parser."""
    p_red = sub.add_parser(
        "redact",
        help="Strip personal details from an export to share it",
        description="Strip the account numbers, names and contact "
             "details it recognises from broker exports (row shapes "
             "kept) so a statement can be shared as a parser sample or "
             "bug report — review the output before sharing. With no "
             "FILE, copy the project's inputs/ to inputs_redact/ and "
             "redact the copy (inputs/ is never changed).")
    p_red.add_argument("files", nargs="*", metavar="FILE",
                       help="Exports to redact (each copied as "
                            "NAME.redacted.EXT). None: copy the project's "
                            "inputs/ to inputs_redact/ and redact the copy")
    p_red.add_argument("--out", metavar="DIR",
                       help="Write redacted copies here (default: beside "
                            "each input as NAME.redacted.EXT; with no FILE, "
                            "the redacted inputs tree: inputs_redact/)")
    p_red.add_argument("--also", action="append", default=[],
                       metavar="REGEX",
                       help="Extra pattern to replace with REDACTED "
                            "(repeatable)")
    p_red.add_argument("--no-denylist", action="store_true",
                       help="Ignore ~/.config/taxjson/pii-denylist")
    p_red.add_argument("--force", action="store_true",
                       help="Overwrite an existing redacted copy (with no "
                            "FILE: replace inputs_redact/)")
    p_red.add_argument("--check", action="store_true",
                       help="Report only; write nothing; exit 1 if "
                            "anything would be redacted")
    p_red.set_defaults(func=cmd_redact)
