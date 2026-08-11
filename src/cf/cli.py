import argparse
import subprocess
import sys


def main():
    p = argparse.ArgumentParser(prog="cf", description="Monarch Money -> CoolClimate Goods & Services")
    p.add_argument("command", choices=["fetch", "import", "aggregate", "site", "serve", "all", "classify"])
    p.add_argument("path", nargs="?", help="CSV path for `cf import`")
    p.add_argument("--months", type=int, default=12, help="trailing window in months (default 12)")
    p.add_argument("--no-open", action="store_true", help="don't open the site in a browser")
    p.add_argument("--port", type=int, default=8742, help="port for `cf serve` (default 8742)")
    args = p.parse_args()

    if args.command == "serve":
        from . import server
        if not args.no_open and sys.platform == "darwin":
            subprocess.Popen(["open", f"http://127.0.0.1:{args.port}"])
        server.run(port=args.port)
        return

    if args.command == "classify":
        if not args.path:
            p.error("cf classify needs a CSV path")
        from . import classify
        classify.run(args.path)
        return
    if args.command == "import":
        if not args.path:
            p.error("cf import needs a CSV path")
        from . import csv_import
        csv_import.run(args.path)
    if args.command in ("fetch", "all"):
        from . import fetch
        fetch.run(months=args.months)
    if args.command in ("aggregate", "import", "all"):
        from . import aggregate
        aggregate.run()
    if args.command in ("site", "import", "all"):
        from . import build_site
        out = build_site.run()
        if not args.no_open and sys.platform == "darwin":
            subprocess.run(["open", str(out)])


if __name__ == "__main__":
    main()
