"""Command-line entry point."""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import pathlib
import re
import sys

from . import __version__
from .export import IncompleteExport, build_guide
from .fetch import FetchError, make_session
from .kml import write_kml, write_kmz
from .model import Guide
from .resolve import ResolveError


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^\w\s.-]", "", name).strip().replace(" ", "-")
    return cleaned or "guide"


def _as_json(guide: Guide) -> str:
    payload = {
        "name": guide.name,
        "source_url": guide.source_url,
        "places": [
            {**dataclasses.asdict(p), "address": p.address, "maps_url": p.maps_url}
            for p in guide.places
        ],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mapplizer",
        description="Export an Apple Maps guide share link to KML/KMZ.",
    )
    parser.add_argument("url", help="guide share URL, e.g. https://maps.apple/ug/...")
    parser.add_argument(
        "-o", "--output", type=pathlib.Path, help="output path (default: <guide name>.<ext>)"
    )
    parser.add_argument(
        "-f",
        "--format",
        choices=("kml", "kmz", "json"),
        default="kml",
        help="output format (default: kml)",
    )
    parser.add_argument(
        "--photos",
        action="store_true",
        help="download and embed photos (KMZ only)",
    )
    parser.add_argument(
        "--lang", default="en-US", help="Accept-Language for place data (default: en-US)"
    )
    parser.add_argument(
        "--cache",
        type=pathlib.Path,
        help="directory to cache place records in, keyed by muid",
    )
    parser.add_argument(
        "--chunk-size", type=int, help="places per request (default: 10)"
    )
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="write output even if some places fail to resolve",
    )
    parser.add_argument("-q", "--quiet", action="store_true", help="only report errors")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    parser.add_argument("--version", action="version", version=f"mapplizer {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    level = logging.DEBUG if args.verbose else logging.ERROR if args.quiet else logging.INFO
    logging.basicConfig(level=level, format="%(message)s", stream=sys.stderr)

    if args.photos and args.format != "kmz":
        print("--photos requires --format kmz", file=sys.stderr)
        return 2

    session = make_session(args.lang)
    try:
        guide = build_guide(
            args.url,
            language=args.lang,
            cache_dir=args.cache,
            chunk_size=args.chunk_size,
            session=session,
            strict=not args.allow_partial,
        )
    except IncompleteExport as exc:
        print(f"error: {exc}. Re-run with --allow-partial to export anyway.", file=sys.stderr)
        return 1
    except (ResolveError, FetchError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    output = args.output or pathlib.Path(f"{_safe_filename(guide.name)}.{args.format}")

    if args.format == "json":
        output.write_text(_as_json(guide), encoding="utf-8")
    elif args.format == "kmz":
        write_kmz(guide, output, embed_photos=args.photos, session=session)
    else:
        write_kml(guide, output)

    if not args.quiet:
        print(f"Wrote {len(guide.places)} places to {output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
