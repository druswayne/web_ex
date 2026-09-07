"""Find a royalty-free lo-fi track and mix it under the Instagram reel."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"
OUT_DIR = WEB / "demo_reel"
VIDEO = OUT_DIR / "instagram_demo.mp4"
MUSIC = OUT_DIR / "bg_lofi.mp3"
MIXED = OUT_DIR / "instagram_demo_music.mp4"

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}

CANDIDATES = [
    # Mixkit License (free for videos, no attribution required)
    "https://assets.mixkit.co/music/preview/mixkit-sleepy-cat-135.mp3",
    "https://assets.mixkit.co/music/download/mixkit-sleepy-cat-135.mp3",
    "https://assets.mixkit.co/music/preview/mixkit-sweet-september-1090.mp3",
    "https://assets.mixkit.co/music/download/mixkit-sweet-september-1090.mp3",
    # Kevin MacLeod, CC BY 3.0
    "https://incompetech.com/music/royalty-free/mp3-royaltyfree/Carefree.mp3",
    "https://incompetech.com/music/royalty-free/mp3-royaltyfree/Easy%20Lemon.mp3",
    "https://incompetech.com/music/royalty-free/mp3-royaltyfree/Wallpaper.mp3",
    # Bensound free license (attribution)
    "https://www.bensound.com/bensound-music/bensound-ukulele.mp3",
    "https://www.bensound.com/bensound-music/bensound-acousticbreeze.mp3",
    "https://www.bensound.com/bensound-music/bensound-onceagain.mp3",
]


def get(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def looks_like_audio(data: bytes, url: str) -> bool:
    if len(data) < 40_000:
        return False
    if data[:3] == b"ID3" or data[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return True
    if data[4:8] == b"ftyp":
        return True
    if url.lower().endswith(".mp3") and data[:4] != b"<!DO" and b"<html" not in data[:200].lower():
        return len(data) > 100_000
    return False


def scrape_mixkit() -> list[str]:
    urls: list[str] = []
    pages = [
        "https://mixkit.co/free-stock-music/lo-fi-beats/",
        "https://mixkit.co/free-stock-music/downtempo/",
    ]
    for page in pages:
        try:
            html = get(page).decode("utf-8", "replace")
        except Exception as exc:
            print("scrape fail", page, exc)
            continue
        urls += re.findall(r"https://assets\.mixkit\.co/music/[^\s\"']+\.mp3", html)
        for slug in re.findall(r"mixkit-[a-z0-9-]+-\d+", html):
            urls.append(f"https://assets.mixkit.co/music/preview/{slug}.mp3")
            urls.append(f"https://assets.mixkit.co/music/download/{slug}.mp3")
        print("page", page, "html", len(html), "hits", len(urls))
    # unique preserve order
    seen = set()
    out = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def ffmpeg_path() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def download_music() -> tuple[Path, str]:
    tried = scrape_mixkit() + CANDIDATES
    last_err = None
    for url in tried:
        try:
            print("try", url)
            data = get(url)
            if not looks_like_audio(data, url):
                print("  skip, not audio", len(data))
                continue
            MUSIC.write_bytes(data)
            print("saved", MUSIC, len(data), "from", url)
            return MUSIC, url
        except Exception as exc:
            last_err = exc
            print("  fail", exc)
    raise RuntimeError(f"не удалось скачать музыку: {last_err}")


def mix(music: Path, source_url: str) -> None:
    ff = ffmpeg_path()
    probe = subprocess.run(
        [ff, "-i", str(VIDEO)],
        capture_output=True,
        text=True,
        check=False,
    )
    print(probe.stderr[-800:])
    # Quiet background: ~18% volume, fade in 0.8s, fade out last 2s.
    # Trim/pad to video length. AAC for Instagram.
    cmd = [
        ff,
        "-y",
        "-i",
        str(VIDEO),
        "-i",
        str(music),
        "-filter_complex",
        (
            "[1:a]volume=0.18,afade=t=in:st=0:d=0.8,"
            "afade=t=out:st=58:d=2[a]"
        ),
        "-map",
        "0:v",
        "-map",
        "[a]",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-shortest",
        "-movflags",
        "+faststart",
        str(MIXED),
    ]
    print("ffmpeg", " ".join(cmd))
    subprocess.run(cmd, check=True)
    meta = {
        "video": str(MIXED),
        "music_file": str(music),
        "source": source_url,
        "volume": 0.18,
    }
    (OUT_DIR / "music_credit.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("READY", MIXED, MIXED.stat().st_size)


if __name__ == "__main__":
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if not VIDEO.exists():
        sys.exit(f"нет видео: {VIDEO}")
    path, url = download_music()
    mix(path, url)
