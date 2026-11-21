"""Create synthesized media using the build machine's FFmpeg."""

import argparse
import subprocess
from pathlib import Path


def generate(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)

    def encode(name: str, *arguments: str) -> None:
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *arguments, str(output / name)],
            check=True,
        )

    encode("bgm.mp3", "-f", "lavfi", "-i", "sine=f=440:d=2", "-c:a", "libmp3lame")
    encode("effect.wav", "-f", "lavfi", "-i", "sine=f=880:d=0.6", "-c:a", "pcm_s16le")
    picture = ("-f", "lavfi", "-i", "testsrc2=s=320x180:r=30:d=2")
    h264 = ("-c:v", "libx264", "-pix_fmt", "yuv420p")
    encode(
        "credits.mp4", *picture, "-f", "lavfi", "-i", "sine=f=660:d=2",
        *h264, "-c:a", "aac", "-shortest",
    )
    encode("silent.mp4", *picture, *h264, "-an")
    (output / "broken.mp4").write_bytes(b"GalGame Maker intentionally invalid media fixture\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    generate(parser.parse_args().output)
