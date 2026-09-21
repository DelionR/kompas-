from __future__ import annotations
import argparse, json, sys
from pathlib import Path
from PIL import Image, ImageChops, ImageStat


def describe(path: Path):
    img = Image.open(path).convert("RGB")
    gray = img.convert("L")
    stat = ImageStat.Stat(gray)
    lo, hi = gray.getextrema()
    return {
        "path": str(path), "width": img.width, "height": img.height,
        "bytes": path.stat().st_size, "mean": stat.mean[0],
        "stddev": stat.stddev[0], "min": lo, "max": hi,
        "dynamic_range": hi - lo,
    }


def check(path: Path):
    d = describe(path)
    reasons = []
    if d["width"] < 300 or d["height"] < 200: reasons.append("image_too_small")
    if d["bytes"] < 2000: reasons.append("png_too_small")
    if d["dynamic_range"] < 8 or d["stddev"] < 1.5: reasons.append("image_nearly_uniform_or_blank")
    d["pass"] = not reasons; d["reasons"] = reasons
    return d


def compare(a: Path, b: Path):
    ia = Image.open(a).convert("RGB"); ib = Image.open(b).convert("RGB")
    if ia.size != ib.size:
        return {"pass": True, "size_changed": True, "a_size": ia.size, "b_size": ib.size, "mean_abs_diff": None}
    diff = ImageChops.difference(ia, ib)
    stat = ImageStat.Stat(diff)
    mad = sum(stat.mean) / 3.0
    # A fitted CAD model should produce much more than tiny timestamp/antialias noise.
    return {"pass": mad >= 0.15, "size_changed": False, "a_size": ia.size, "b_size": ib.size, "mean_abs_diff": mad}


def main():
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("check"); p.add_argument("path")
    p = sub.add_parser("compare"); p.add_argument("a"); p.add_argument("b")
    args = ap.parse_args()
    result = check(Path(args.path)) if args.cmd == "check" else compare(Path(args.a), Path(args.b))
    print(json.dumps(result, ensure_ascii=False))
    if not result.get("pass"): sys.exit(2)


if __name__ == "__main__": main()
