# Cutpoint Audit

**Know which planned cuts miss a video keyframe before using stream copy.** Feed Cutpoint Audit FFprobe JSON and a list of cut times. It shows the key-marked packets on either side of each cut and flags requests outside your time tolerance.

## Try the demo

Download the standalone script and run it with Python 3.11 or newer. The demo needs only Python. [Download Cutpoint Audit](https://github.com/4onv/cutpoint-audit/releases/latest)

```sh
python cutpoint_audit.py demo
```

The demo uses invented timestamps. A cut at 1.98s is 20ms before a key-marked packet and passes a 50ms tolerance. A cut at 3.45s is 550ms from the nearest candidate and fails.

## Check a video

Export packet metadata for one video stream. This checks the exact integer presentation timestamps in the stream’s time base.

```sh
ffprobe -v error -select_streams v:0 -show_packets -show_streams -show_entries "stream=index,codec_type,time_base,start_pts:packet=stream_index,pts,flags" -of json input.mp4 > probe.json
```

Use UTF-8 redirection. In Windows PowerShell 5.1, pipe the FFprobe output to `Set-Content -Encoding UTF8 probe.json`.

Save your requested offsets as `cuts.json`. Offsets are seconds from the selected stream's `start_pts`. IDs must be unique.

```json
[
  {"id": "intro", "seconds": "1.98"},
  {"id": "product", "seconds": "3.45"}
]
```

```sh
python cutpoint_audit.py check probe.json cuts.json --policy nearest --tolerance-ms 50
python cutpoint_audit.py check probe.json cuts.json --policy before --tolerance-ms 80 --json
```

Choose `nearest` (ties prefer the earlier packet), `before` (at or before the requested cut), or `after` (at or after). JSON contains both bracketing packets, the chosen packet, its signed offset in milliseconds, and the result for each cut. Pass `--origin-pts` in stream ticks to choose a different timeline origin or when FFprobe has no `start_pts`.

| Exit | Meaning |
| --- | --- |
| 0 | All requested cuts meet the policy and tolerance. A successful demo also exits 0. |
| 1 | At least one cut is outside tolerance or has no candidate in the requested direction. |
| 2 | The command or input metadata is unusable. |

Tolerance is inclusive and uses exact rational arithmetic. Display values are rounded to at most nine decimal places. The JSON report includes the original integer packet PTS and the stream time base for exact positions.

## Scope

The script reads FFprobe JSON and does not edit video. It skips discarded and corrupt packets, sorts by PTS, and reports unique key-marked packet times. This is a preflight check for one video stream. Key flags alone cannot guarantee independent decoding across an open GOP, muxer behavior, exact output boundaries, or audio alignment. Use the same stream and timeline as your cutting workflow. The probe file limit is 64 MiB and each check supports up to 10,000 cuts. [FFprobe documentation](https://ffmpeg.org/ffprobe.html) · [FFmpeg segment muxer](https://ffmpeg.org/ffmpeg-formats.html#segment_002c-stream_005fsegment_002c-ssegment)

```sh
python -m unittest discover -s tests -v
python tests/native_smoke.py
```

The native smoke test needs FFmpeg with libx264 and FFprobe. It generates synthetic footage in a temporary folder. [November 23 research and alternatives](docs/research-2026-11-23.md)
