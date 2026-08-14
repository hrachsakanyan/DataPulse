"""DataPulse pipeline runner.

    python main.py                     # full run, using cached raw JSON when fresh
    python main.py --refresh           # ignore the cache and re-fetch
    python main.py --country GEO       # focus the figures on another country
    python main.py --themes light dark # render both colour themes
    python main.py --no-report         # skip the Markdown export
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

from src import analyze, clean, config, fetch, report, visualize

log = logging.getLogger("datapulse")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="datapulse",
        description="Fetch World Bank health indicators, clean them, analyse trends, and chart the result.",
    )
    parser.add_argument("--refresh", action="store_true", help="bypass the raw JSON cache")
    parser.add_argument("--country", default=config.FOCUS_COUNTRY, help="ISO-3 code to focus on (default: %(default)s)")
    parser.add_argument("--start-year", type=int, default=config.START_YEAR)
    parser.add_argument("--end-year", type=int, default=config.END_YEAR)
    parser.add_argument("--themes", nargs="+", default=["light"], choices=["light", "dark"])
    parser.add_argument("--no-report", action="store_true", help="skip the Markdown report export")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> int:
    started = time.perf_counter()

    if args.country not in config.COUNTRIES:
        log.error("--country %s is not in config.COUNTRIES %s", args.country, config.COUNTRIES)
        return 2
    config.FOCUS_COUNTRY = args.country

    log.info("=== 1/5 collect ===")
    raw = fetch.fetch_all(
        start_year=args.start_year,
        end_year=args.end_year,
        use_cache=not args.refresh,
    )

    log.info("=== 2/5 clean ===")
    tidy = clean.clean(raw, start_year=args.start_year, end_year=args.end_year)
    wide = clean.to_wide(tidy)
    clean.save(tidy, wide)

    log.info("=== 3/5 analyse ===")
    tables = analyze.analyze(tidy, wide)

    log.info("=== 4/5 visualise ===")
    rendered: dict[str, list] = {}
    for theme_name in args.themes:
        rendered[theme_name] = visualize.render_all(tidy, wide, tables, theme_name=theme_name)

    # The report embeds the light figures when they exist — a Markdown file
    # is read on a white page far more often than a dark one.
    figures = rendered.get("light") or next(iter(rendered.values()), [])

    log.info("=== 5/5 export ===")
    if args.no_report:
        log.info("report      skipped (--no-report)")
    else:
        report.build_report(tidy, tables, figures, focus=args.country)

    elapsed = time.perf_counter() - started
    total_figures = sum(len(paths) for paths in rendered.values())
    log.info("done in %.1fs — %d rows, %d figures", elapsed, len(tidy), total_figures)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)-7s %(message)s",
    )

    try:
        return run(args)
    except fetch.FetchError as exc:
        log.error("collection failed: %s", exc)
        log.error("check the network connection, or re-run without --refresh to use cached data")
        return 1


if __name__ == "__main__":
    sys.exit(main())
