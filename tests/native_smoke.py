"""Check a generated video against FFprobe output with no hand-authored packet data."""

import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory


SCRIPT = Path(__file__).resolve().parents[1] / "cutpoint_audit.py"
with TemporaryDirectory(prefix="cutpoint-synthetic-") as folder:
    folder = Path(folder)
    clip = folder / "synthetic.mp4"
    subprocess.run([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=duration=5:size=96x64:rate=10",
        "-c:v", "libx264", "-x264-params", "keyint=20:min-keyint=20:scenecut=0", "-threads", "1",
        str(clip),
    ], check=True)
    metadata = subprocess.check_output([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_packets", "-show_streams",
        "-show_entries", "stream=index,codec_type,time_base,start_pts:packet=stream_index,pts,flags",
        "-of", "json", str(clip),
    ])
    (folder / "probe.json").write_bytes(metadata)
    (folder / "cuts.json").write_text(
        '[{"id":"synthetic-near","seconds":"1.98"},{"id":"synthetic-far","seconds":"3.45"}]',
        encoding="utf-8",
    )
    run = subprocess.run([
        sys.executable, "-I", str(SCRIPT), "check", "probe.json", "cuts.json", "--json",
    ], cwd=folder, capture_output=True, check=False)
    assert run.returncode == 1, (run.returncode, run.stderr)
    result = json.loads(run.stdout)
    assert result["key_packet_count"] == 3, result
    assert result["misses"] == 1, result
    assert [cut["chosen"]["offset_ms"] for cut in result["cuts"]] == ["20", "550"], result

print("FFmpeg and FFprobe smoke passed on generated footage.")
