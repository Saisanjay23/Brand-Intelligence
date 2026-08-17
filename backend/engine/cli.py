"""Command line for the standalone engine.

    python -m backend.engine platforms
    python -m backend.engine discover --keywords "Acme,Acme Corp" --out hits.json
    python -m backend.engine analyze  --urls-file urls.txt --target Acme --out rows.json

A thin wrapper over `runner.discover` / `runner.analyze`, every flag maps
to a field on `DiscoveryRequest` / `AnalysisRequest`, and `--input` takes
the whole request as JSON instead. There is no behaviour here that the
Python API does not also expose; anything else would make the CLI a second
implementation to keep correct.

EXIT CODES, because this is meant to be driven by a scheduler that can only
see the exit status:

    0   ran, and at least one platform produced results
    1   ran, but nothing usable came back (no credentials, every platform
        failed), the details are in the output file and on stderr
    2   bad usage (unparseable input, unwritable output path)
    130 interrupted
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Optional

from backend.engine import sinks
from backend.engine.credentials import CredentialStore, default_dir
from backend.engine.models import AnalysisRequest, DiscoveryRequest, EngineResult
from backend.engine.runner import analyze, discover

EXIT_OK, EXIT_EMPTY, EXIT_USAGE, EXIT_INTERRUPT = 0, 1, 2, 130


def _csv_list(value: str) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def _load_input(path: str) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SystemExit(f"cannot read --input {path}: {type(e).__name__}: {e}")


def _read_urls(args) -> list[str]:
    """URLs from --urls, --urls-file, or stdin when the file is "-".

    Blank lines and `#` comments are skipped so a hand-maintained URL list
    can carry notes without breaking the run.
    """
    urls = list(args.urls or [])
    if args.urls_file:
        if args.urls_file == "-":
            text = sys.stdin.read()
        else:
            try:
                text = Path(args.urls_file).read_text(encoding="utf-8")
            except OSError as e:
                raise SystemExit(f"cannot read --urls-file {args.urls_file}: {e}")
        urls += [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    return urls


def _progress(quiet: bool):
    """Progress goes to stderr, never stdout, stdout may be carrying the
    results themselves when no --out is given, and a caller piping them into
    `jq` must not get log lines mixed in."""
    if quiet:
        return None
    return lambda line: print(line, file=sys.stderr, flush=True)


def _emit(result: EngineResult, args) -> None:
    if args.out:
        try:
            sinks.write(result, args.out, args.format)
        except (OSError, ValueError) as e:
            raise SystemExit(f"cannot write --out {args.out}: {type(e).__name__}: {e}")
    else:
        json.dump(result.to_dict(), sys.stdout, indent=2, ensure_ascii=False, default=str)
        sys.stdout.write("\n")


async def _maybe_mongo(result: EngineResult, args) -> None:
    """--save-to-mongo is best-effort ON TOP OF the normal output, never
    instead of it. A database that has gone away must not cost the caller
    a scrape that already succeeded, so this reports the failure and lets
    the run stand."""
    if not args.save_to_mongo:
        return
    try:
        stats = await sinks.to_mongo(result, args.client_id)
        print(f"mongo: {stats['saved']} saved, {stats['new']} new", file=sys.stderr)
    except Exception as e:
        print(f"mongo write FAILED ({type(e).__name__}: {e}) -- results are still in the output", file=sys.stderr)


# ------------------------------------------------------------ subcommands


def _cmd_platforms(args) -> int:
    store = CredentialStore(Path(args.creds) if args.creds else None)
    report = store.report()
    if args.json:
        json.dump({"credentials_dir": str(store.dir), "platforms": report},
                  sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return EXIT_OK if any(p["state"] == "ready" for p in report) else EXIT_EMPTY

    print(f"credentials dir: {store.dir}"
          f"{'' if store.dir.is_dir() else '   (does not exist yet)'}\n")
    width = max(len(p["platform"]) for p in report)
    for p in report:
        mark = "ok " if p["state"] == "ready" else "-- "
        line = f"  [{mark}] {p['platform']:<{width}}  {p['state']:<10} {p['kind']}"
        if p["state"] == "ready" and p["sessions"] > 1:
            line += f"  ({p['sessions']} credentials)"
        print(line)
        if p["fix"]:
            print(f"        -> {p['fix']}")
        if p["state"] == "ready" and p["analysis_stub"]:
            print("        -> note: analysis is a stub on this platform; discovery works")
    ready = [p for p in report if p["state"] == "ready"]
    print(f"\n{len(ready)} of {len(report)} platform(s) ready.")
    return EXIT_OK if ready else EXIT_EMPTY


async def _cmd_discover(args) -> int:
    payload = _load_input(args.input) if args.input else {}
    if args.keywords:
        payload["keywords"] = _csv_list(args.keywords)
    if args.platforms:
        payload["platforms"] = _csv_list(args.platforms)
    for flag in ("target", "max_results", "max_seconds", "concurrency", "platform_concurrency", "client_id"):
        value = getattr(args, flag, None)
        if value not in (None, ""):
            payload[flag] = value
    if args.headful:
        payload["headful"] = True

    request = DiscoveryRequest.from_dict(payload)
    if not request.keywords:
        raise SystemExit("no keywords -- pass --keywords or an --input file containing them")

    store = CredentialStore(Path(args.creds) if args.creds else None)
    result = await discover(request, store, _progress(args.quiet))
    _emit(result, args)
    await _maybe_mongo(result, args)
    for err in result.errors:
        print(f"warning: {err}", file=sys.stderr)
    return EXIT_OK if result.ok else EXIT_EMPTY


async def _cmd_analyze(args) -> int:
    payload = _load_input(args.input) if args.input else {}
    urls = _read_urls(args)
    if urls:
        payload["urls"] = urls
    for flag in ("target", "official_feed", "platform", "evidence_dir", "delay", "concurrency", "client_id"):
        value = getattr(args, flag, None)
        if value not in (None, ""):
            payload[flag] = value
    if args.headful:
        payload["headful"] = True

    request = AnalysisRequest.from_dict(payload)
    if not request.urls:
        raise SystemExit("no urls -- pass --urls, --urls-file, or an --input file containing them")

    store = CredentialStore(Path(args.creds) if args.creds else None)
    result = await analyze(request, store, _progress(args.quiet))
    _emit(result, args)
    await _maybe_mongo(result, args)
    for err in result.errors:
        print(f"warning: {err}", file=sys.stderr)
    return EXIT_OK if result.ok else EXIT_EMPTY


# ---------------------------------------------------------------- parsing


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m backend.engine",
        description="Brand Intelligence engine, standalone: input in, results out. "
                    "No database, no frontend, no HTTP server required.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--creds", default="", metavar="DIR",
                       help=f"credentials directory (default: {default_dir()})")
        p.add_argument("--input", default="", metavar="FILE",
                       help="the whole request as JSON; individual flags override it")
        p.add_argument("--out", default="", metavar="FILE",
                       help="write results here (default: JSON to stdout)")
        p.add_argument("--format", default="", choices=("", *sinks.WRITERS),
                       help="output format (default: inferred from --out's extension)")
        p.add_argument("--client-id", default="", metavar="ID",
                       help="only used to label records for --save-to-mongo")
        p.add_argument("--save-to-mongo", action="store_true",
                       help="ALSO write results to Mongo, if one is reachable")
        p.add_argument("--headful", action="store_true", help="show the browser")
        p.add_argument("--quiet", action="store_true", help="no progress lines on stderr")

    p_platforms = sub.add_parser("platforms", help="which platforms have usable credentials")
    p_platforms.add_argument("--creds", default="", metavar="DIR")
    p_platforms.add_argument("--json", action="store_true", help="machine-readable output")

    p_discover = sub.add_parser("discover", help="keywords -> candidate profiles")
    common(p_discover)
    p_discover.add_argument("--keywords", default="", help="comma-separated")
    p_discover.add_argument("--platforms", default="",
                            help="comma-separated; default is every ready platform")
    p_discover.add_argument("--target", default="",
                            help="the brand/person being protected (default: the first keyword)")
    p_discover.add_argument("--max-results", type=int, dest="max_results", default=None,
                            help="cap per keyword+tab; 0 = uncapped")
    p_discover.add_argument("--max-seconds", type=float, dest="max_seconds", default=None,
                            help="cap per sweep")
    p_discover.add_argument("--concurrency", type=int, default=None,
                            help="keyword sweeps in flight within one platform")
    p_discover.add_argument("--platform-concurrency", type=int, dest="platform_concurrency", default=None,
                            help="platforms swept at once (default 1: one browser at a time)")

    p_analyze = sub.add_parser("analyze", help="profile urls -> scored rows")
    common(p_analyze)
    p_analyze.add_argument("--urls", nargs="*", default=[])
    p_analyze.add_argument("--urls-file", dest="urls_file", default="",
                           help='one URL per line; "-" reads stdin')
    p_analyze.add_argument("--target", default="", help="the brand/person being protected")
    p_analyze.add_argument("--official-feed", dest="official_feed", default="",
                           help="the client's genuine profile URL, for comparison")
    p_analyze.add_argument("--platform", default="",
                           help="force one platform instead of inferring it per URL")
    p_analyze.add_argument("--evidence-dir", dest="evidence_dir", default="",
                           help='where screenshots go; "-" disables capture')
    p_analyze.add_argument("--delay", type=float, default=None, help="seconds between profiles")
    p_analyze.add_argument("--concurrency", type=int, default=None)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "platforms":
            return _cmd_platforms(args)
        handler = {"discover": _cmd_discover, "analyze": _cmd_analyze}[args.command]
        return asyncio.run(handler(args))
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return EXIT_INTERRUPT
    except SystemExit as e:
        # argparse and our own usage errors both land here; keep the message
        # but normalise the code so a scheduler can tell usage from empty
        if isinstance(e.code, str):
            print(e.code, file=sys.stderr)
            return EXIT_USAGE
        return int(e.code or EXIT_OK)
