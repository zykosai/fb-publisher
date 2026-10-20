"""Create a clean source ZIP without runtime data. No Facebook access."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from bike_publisher.distribution import build_archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'dist'/'FB-Publisher-source.zip')
    args = parser.parse_args()
    count, digest = build_archive(ROOT, args.output)
    print(f'Archive: {args.output.resolve()}')
    print(f'Public files: {count}; no database, profiles, photos, logs or .env')
    print(f'SHA256: {digest}')


if __name__ == '__main__':
    main()
