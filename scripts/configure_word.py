"""Configure a locally trusted certificate; does not change the OS trust store."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from exhibit.project import ROOT


from exhibit.word_setup import configure


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--cert', type=Path, default=Path.home()/'.office-addin-dev-certs/localhost.crt')
    parser.add_argument('--key', type=Path, default=Path.home()/'.office-addin-dev-certs/localhost.key')
    parser.add_argument('--port', type=int, default=8769)
    parser.add_argument('--data-dir', type=Path, default=ROOT)
    args = parser.parse_args()
    print(configure(args.data_dir, args.cert, args.key, args.port))
