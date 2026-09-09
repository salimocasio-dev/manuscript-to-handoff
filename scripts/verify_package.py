"""Independently inspect an exported ZIP against external source and approval."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mth.validation import validate_package

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("package", type=Path)
parser.add_argument("--source", type=Path, required=True, help="Independent revision JSON, not a self-declared source inside the ZIP")
parser.add_argument("--approval", type=Path, required=True, help="Independent approval JSON")
parser.add_argument("--candidate", type=Path, help="Original validated candidate JSON for complete candidate matching")
args = parser.parse_args()
load = lambda path: json.loads(path.read_bytes().decode("utf-8"))
report = validate_package(args.package.read_bytes(), load(args.source), load(args.approval),
                          load(args.candidate) if args.candidate else None)
print(json.dumps(report, indent=2, ensure_ascii=False))
sys.exit(0 if report["passed"] else 1)
