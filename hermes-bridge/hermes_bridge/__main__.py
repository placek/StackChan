import argparse
import logging

from aiohttp import web

from .config import load_config
from .server import create_app


def main():
    parser = argparse.ArgumentParser(
        prog="hermes-bridge",
        description="Bridge between StackChan devices and a self-hosted Hermes agent",
    )
    parser.add_argument(
        "-c", "--config", default="config.yaml",
        help="path to the YAML config file (default: config.yaml)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        config = load_config(args.config)
    except FileNotFoundError:
        # Fall back to defaults when the default config path is absent
        if args.config != "config.yaml":
            raise
        config = load_config(None)

    app = create_app(config)
    web.run_app(app, host=config["server"]["host"], port=config["server"]["port"])


if __name__ == "__main__":
    main()
