"""Check public source files; print paths/reasons, never matching secret values."""
import argparse
from pathlib import Path
import sys
import subprocess

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from bike_publisher.distribution import check_sources


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--git', action='store_true', help='Also verify every tracked/staged Git path')
    args = parser.parse_args()
    try:
        files = check_sources(ROOT, check_git=args.git)
    except (ValueError, OSError) as exc:
        parser.exit(1, str(exc)+'\n')
    except subprocess.CalledProcessError:
        parser.exit(1, 'Git could not inspect this repository. Check Git installation and folder ownership; no files were published.\n')
    print(f'Distribution check passed: {len(files)} allowlisted source files')
