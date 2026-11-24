#!/usr/bin/env python3
"""Check planned stream-copy cuts against FFprobe's key-packet timestamps."""

import argparse
from bisect import bisect_left, bisect_right
from fractions import Fraction
import json
from pathlib import Path
import re
import sys


MAX_PROBE_BYTES = 64 * 1024 * 1024
MAX_CUT_BYTES = 1024 * 1024
POLICIES = ("nearest", "before", "after")


class InputError(ValueError):
    pass


def read_json(filename, size_limit):
    with Path(filename).open("rb") as handle:
        data = handle.read(size_limit + 1)
    if len(data) > size_limit:
        raise InputError(f"input exceeds {size_limit} bytes")
    # Preserve the spelling of JSON decimal tokens for exact arithmetic.
    return json.loads(data.decode("utf-8-sig"), parse_float=str)


def integer(value, label):
    if type(value) is int:
        result = value
    elif isinstance(value, str) and re.fullmatch(r"-?[0-9]{1,19}", value):
        result = int(value)
    else:
        raise InputError(f"{label} must be an integer")
    if not -(2**63) <= result < 2**63:
        raise InputError(f"{label} is outside the signed 64-bit range")
    return result


def decimal(value, label):
    if type(value) is int:
        if 0 <= value < 10**10:
            return Fraction(value)
    elif isinstance(value, str) and re.fullmatch(r"[0-9]{1,10}(?:\.[0-9]{1,9})?", value):
        return Fraction(value)
    raise InputError(f"{label} must be nonnegative, with up to 10 whole and 9 fractional digits")


def timeline(probe, origin_override):
    if not isinstance(probe, dict):
        raise InputError("probe input must be an FFprobe JSON object")
    streams = probe.get("streams")
    packets = probe.get("packets")
    if (
        not isinstance(streams, list) or len(streams) != 1
        or not isinstance(streams[0], dict) or streams[0].get("codec_type") != "video"
        or not isinstance(packets, list)
    ):
        raise InputError("expected one selected video stream and a packets array; see the FFprobe command in the README")
    stream = streams[0]
    index = integer(stream.get("index"), "stream index")
    if index < 0:
        raise InputError("stream index must be nonnegative")
    time_base = stream.get("time_base")
    if not isinstance(time_base, str) or not re.fullmatch(r"[0-9]{1,10}/[0-9]{1,10}", time_base):
        raise InputError("stream time_base must be a positive integer ratio")
    numerator, denominator = map(int, time_base.split("/"))
    if not numerator or not denominator:
        raise InputError("stream time_base must be a positive integer ratio")
    if origin_override is None and stream.get("start_pts") in (None, "N/A"):
        raise InputError("stream start_pts is unavailable; supply --origin-pts in stream ticks")
    origin = integer(stream["start_pts"], "stream start_pts") if origin_override is None else origin_override
    keys = set()
    for number, packet in enumerate(packets, 1):
        if not isinstance(packet, dict) or integer(packet.get("stream_index"), f"packet {number} stream_index") != index:
            raise InputError(f"packet {number} does not belong to the selected stream")
        flags = packet.get("flags")
        if not isinstance(flags, str) or not flags:
            raise InputError(f"packet {number} is missing flags")
        if "K" in flags and "D" not in flags and "C" not in flags:
            keys.add(integer(packet.get("pts"), f"key packet {number} PTS"))
    if not keys:
        raise InputError("no usable key-marked packets were found")
    return index, Fraction(numerator, denominator), origin, sorted(keys)


def cut_list(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 10_000:
        raise InputError("cuts must be a JSON array with 1 to 10000 items")
    cuts = []
    ids = set()
    for number, item in enumerate(value, 1):
        if not isinstance(item, dict):
            raise InputError(f"cut {number} must be an object")
        label = item.get("id")
        if not isinstance(label, str) or not label.strip() or len(label) > 120:
            raise InputError(f"cut {number} needs a nonempty id of at most 120 characters")
        if label in ids:
            raise InputError(f"cut {number} repeats an id")
        ids.add(label)
        cuts.append((label, decimal(item.get("seconds"), f"cut {number} seconds")))
    return cuts


def display_number(number):
    """Render rational positions to nine decimal places without float conversion."""
    scaled = round(number * 10**9)
    whole, remainder = divmod(abs(scaled), 10**9)
    sign = "-" if scaled < 0 else ""
    return f"{sign}{whole}.{remainder:09d}".rstrip("0").rstrip(".")


def packet_position(pts, origin, time_base, requested):
    if pts is None:
        return None
    seconds = (pts - origin) * time_base
    return {
        "pts": pts,
        "seconds": display_number(seconds),
        "offset_ms": display_number((seconds - requested) * 1000),
    }


def evaluate(probe, cuts, tolerance_ms, policy, origin_override=None, synthetic=False):
    index, time_base, origin, keys = timeline(probe, origin_override)
    results = []
    for label, requested in cut_list(cuts):
        target = origin + requested / time_base
        left = bisect_right(keys, target) - 1
        right = bisect_left(keys, target)
        before = keys[left] if left >= 0 else None
        after = keys[right] if right < len(keys) else None
        if policy == "before":
            chosen = before
        elif policy == "after":
            chosen = after
        else:
            chosen = min(
                (pts for pts in (before, after) if pts is not None),
                key=lambda pts: (abs(pts - target), pts),
            )
        within = chosen is not None and abs(chosen - target) * time_base * 1000 <= tolerance_ms
        results.append({
            "id": label,
            "requested_seconds": display_number(requested),
            "before": packet_position(before, origin, time_base, requested),
            "after": packet_position(after, origin, time_base, requested),
            "chosen": packet_position(chosen, origin, time_base, requested),
            "status": "within_tolerance" if within else "outside_tolerance" if chosen is not None else "no_candidate",
        })
    return {
        "version": 1,
        "synthetic": synthetic,
        "stream_index": index,
        "time_base": str(time_base),
        "origin_pts": origin,
        "key_packet_count": len(keys),
        "policy": policy,
        "tolerance_ms": display_number(tolerance_ms),
        "cuts": results,
        "misses": sum(item["status"] != "within_tolerance" for item in results),
    }


def demo():
    probe = {
        "streams": [{"index": 0, "codec_type": "video", "time_base": "1/1000", "start_pts": 500}],
        "packets": [{"stream_index": 0, "pts": pts, "flags": "K__"} for pts in (500, 2500, 4500)],
    }
    cuts = [
        {"id": "intro", "seconds": "1.98"},
        {"id": "product", "seconds": "3.45"},
        {"id": "outro", "seconds": "4"},
    ]
    return evaluate(probe, cuts, Fraction(50), "nearest", synthetic=True)


def render(report):
    if report["synthetic"]:
        print("Synthetic demo with invented packet timestamps")
    print(f"Video stream {report['stream_index']}, {report['key_packet_count']} key timestamps, "
          f"{report['policy']} policy, {report['tolerance_ms']} ms tolerance")
    for item in report["cuts"]:
        chosen = item["chosen"]
        position = "(none)" if chosen is None else f"{chosen['seconds']}s ({chosen['offset_ms']} ms)"
        print(f"{ascii(item['id'])} at {item['requested_seconds']}s -> {position}: {item['status']}")
    print(f"{report['misses']} of {len(report['cuts'])} cut(s) outside policy or tolerance")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="audit cut times using an FFprobe JSON file")
    check.add_argument("probe")
    check.add_argument("cuts")
    check.add_argument("--policy", choices=POLICIES, default="nearest")
    check.add_argument("--tolerance-ms", default="50")
    check.add_argument("--origin-pts", help="override stream start_pts in stream ticks")
    check.add_argument("--json", action="store_true")
    command_demo = commands.add_parser("demo", help="show a labeled synthetic example")
    command_demo.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        if args.command == "demo":
            report = demo()
        else:
            tolerance = decimal(args.tolerance_ms, "tolerance-ms")
            origin = None if args.origin_pts is None else integer(args.origin_pts, "origin-pts")
            report = evaluate(
                read_json(args.probe, MAX_PROBE_BYTES),
                read_json(args.cuts, MAX_CUT_BYTES),
                tolerance, args.policy, origin,
            )
    except (OSError, ValueError, RecursionError) as error:
        print(f"cutpoint-audit: {error}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        render(report)
    return int(args.command == "check" and report["misses"] > 0)


if __name__ == "__main__":
    raise SystemExit(main())
