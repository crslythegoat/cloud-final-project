"""Inspect/create only the four business collections; never changes roles."""

import argparse

from app.core.config import get_settings
from app.mongo_collections import run

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    run(get_settings(), parser.parse_args().apply)
