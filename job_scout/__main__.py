"""CLI entry point: `python -m job_scout [--dry-run] [--no-llm] [--sources ...]`."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from job_scout.config import Settings
from job_scout.emailer import EmailConfigError
from job_scout.models import Source
from job_scout.pipeline import RunOptions, run

ALIASES = {"hn": Source.HACKERNEWS, "x": Source.TWITTER, "li": Source.LINKEDIN}


def parse_sources(value: str) -> set[Source]:
    out: set[Source] = set()
    for name in value.split(","):
        name = name.strip().lower()
        if not name:
            continue
        try:
            out.add(ALIASES.get(name) or Source(name))
        except ValueError:
            valid = ", ".join([s.value for s in Source] + list(ALIASES))
            raise argparse.ArgumentTypeError(f"unknown source {name!r} (valid: {valid})")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="job-scout", description=__doc__)
    parser.add_argument("--config", default="config.yaml", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="don't send email or save history")
    parser.add_argument("--no-llm", action="store_true", help="skip Gemini scoring")
    parser.add_argument("--send-empty", action="store_true", help="email even when nothing matched")
    parser.add_argument("--sources", type=parse_sources, help="comma list, e.g. linkedin,hn,hasjob")
    parser.add_argument("--preview", type=Path, default=Path("out/digest.html"), help="write HTML digest here")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    settings = Settings.load(args.config)
    options = RunOptions(
        dry_run=args.dry_run,
        use_llm=not args.no_llm,
        send_empty=args.send_empty,
        sources=args.sources,
        preview_path=args.preview,
    )

    try:
        result = asyncio.run(run(settings, options))
    except EmailConfigError as exc:
        logging.error("Email not configured: %s", exc)
        return 2

    for note in result.notes:
        logging.info(note)
    if result.stats.errors:
        for source, err in result.stats.errors.items():
            logging.warning("source %s failed: %s", source, err)
    if result.all_sources_failed:
        logging.error("Every source failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
