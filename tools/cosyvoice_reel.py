"""Озвучка Instagram-ролика через CosyVoice 3 (HF Space) с клоном голоса."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"
OUT = WEB / "demo_reel"
VOICE = OUT / "voice"
PROMPT_WAV = VOICE / "prompt.wav"
PROMPT_TXT = VOICE / "prompt.txt"
VIDEO = OUT / "instagram_demo.mp4"
MUSIC = OUT / "bg_lofi.mp3"
FINAL = OUT / "instagram_demo_vo.mp4"
STYLE = "instruct_happy_atempo1.12"
SPEEDUP = 1.12
INSTRUCT = "You are a helpful assistant. 请非常开心地说一句话。<|endofprompt|>"

# Короткие реплики под сцены ролика. «ИИ» пишем как «нейросеть» — CosyVoice тянет «ИИ» в одно «и».
LINES = [
    (0.4, "Показываю, как ученики сами готовятся к экзамену по математике."),
    (6.8, "Вход по логину от учителя. Чужой кабинет не откроешь."),
    (13.2, "Личный кабинет: прогресс по всему курсу."),
    (19.0, "Тридцать пять занятий открываются по мере прохождения. Каждое занятие — три шага: теория, тест, практика."),
    (34.5, "Теорию дочитал — открылся тест. В тесте порог восемьдесят процентов. Видно, где ошибка."),
    (46.5, "Практика своего уровня. Решил в тетради, сфотографировал, отправил. Нейросеть проверяет фото и пишет, где ошибка."),
    (56.2, "Залог хорошей сдачи — качественная подготовка в течение года."),
]


def place_clips(planned: list[tuple[float, Path]]) -> list[tuple[float, Path]]:
    placed: list[tuple[float, Path]] = []
    t_free = 0.2
    for start, wav in planned:
        t = max(start, t_free)
        placed.append((t, wav))
        t_free = t + probe_duration(wav) + 0.2
    return placed


def ffmpeg_exe() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def probe_duration(path: Path) -> float:
    ff = ffmpeg_exe()
    r = subprocess.run([ff, "-i", str(path)], capture_output=True, text=True)
    for line in (r.stderr or "").splitlines():
        if "Duration:" in line:
            hms = line.split("Duration:")[1].split(",")[0].strip()
            h, m, s = hms.split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
    raise RuntimeError(f"нет Duration у {path}")


def connect_client():
    import os

    from gradio_client import Client

    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_HUB_TOKEN")
    if not token:
        sys.exit("Нет HF_TOKEN — задайте токен Hugging Face в переменной окружения.")
    print("подключаюсь к FunAudioLLM/Fun-CosyVoice3-0.5B ...")
    return Client("FunAudioLLM/Fun-CosyVoice3-0.5B", token=token, oauth_token=token)


def generate_one(client, text: str, prompt_text: str, seed: int, out_wav: Path) -> Path:
    from gradio_client import handle_file

    last: Exception | None = None
    for attempt in range(2):
        try:
            print(f"  generate [{attempt+1}] {text[:60]!r}")
            prompt_file = handle_file(str(PROMPT_WAV))
            result = client.predict(
                tts_text=text,
                mode_value="zero_shot",
                prompt_text=prompt_text,
                prompt_wav_upload=prompt_file,
                prompt_wav_record=prompt_file,
                instruct_text=INSTRUCT,
                seed=seed,
                stream=False,
                ui_lang="En",
                api_name="/generate_audio",
            )
            path = result
            if isinstance(result, (list, tuple)):
                path = result[0]
            if isinstance(path, dict):
                path = path.get("path") or path.get("value")
            src = Path(str(path))
            if not src.exists():
                raise FileNotFoundError(src)
            out_wav.parent.mkdir(parents=True, exist_ok=True)
            tmp = out_wav.with_suffix(".raw.wav")
            tmp.write_bytes(src.read_bytes())
            ff = ffmpeg_exe()
            subprocess.run(
                [
                    ff,
                    "-y",
                    "-i",
                    str(tmp),
                    "-af",
                    (
                        "silenceremove=start_periods=1:start_silence=0.05:start_threshold=-38dB:"
                        "stop_periods=1:stop_silence=0.09:stop_threshold=-38dB,"
                        f"atempo={SPEEDUP:.2f}"
                    ),
                    str(out_wav),
                ],
                check=True,
                capture_output=True,
            )
            tmp.unlink(missing_ok=True)
            print("  saved", out_wav, out_wav.stat().st_size)
            return out_wav
        except Exception as exc:
            last = exc
            import traceback

            traceback.print_exc()
            print("  fail", repr(exc))
            time.sleep(4 * (attempt + 1))
    raise RuntimeError(last)


def assemble(clips: list[tuple[float, Path]], video_dur: float) -> Path:
    ff = ffmpeg_exe()
    specs: list[tuple[float, Path, float]] = []
    for start, wav in clips:
        specs.append((start, wav, probe_duration(wav)))
    last_end = specs[-1][0] + specs[-1][2]
    out_dur = max(video_dur, last_end + 1.2)
    pad = max(0.0, out_dur - video_dur)

    filter_parts = []
    inputs = [ff, "-y", "-i", str(VIDEO), "-i", str(MUSIC)]
    for i, (start, wav, dur) in enumerate(specs):
        inputs += ["-i", str(wav)]
        if i + 1 < len(specs):
            max_dur = max(0.4, specs[i + 1][0] - start - 0.1)
            speed = min(1.12, dur / max_dur) if dur > max_dur else 1.0
            play = min(dur / speed, max_dur)
        else:
            speed = 1.0
            play = dur
        fade = min(0.08, play / 8)
        chain = f"[{i+2}:a]aresample=44100,aformat=channel_layouts=stereo"
        if abs(speed - 1.0) > 0.02:
            chain += f",atempo={speed:.3f}"
        chain += (
            f",afade=t=in:st=0:d={fade:.2f},"
            f"afade=t=out:st={max(0, play - fade):.2f}:d={fade:.2f},"
            f"adelay={int(start * 1000)}|{int(start * 1000)},volume=1.15[v{i}]"
        )
        filter_parts.append(chain)
        print(f"  clip {i+1}: start={start:.1f}s dur={dur:.2f}s speed={speed:.2f}")

    n = len(specs)
    mix_ins = "".join(f"[v{i}]" for i in range(n))
    filter_parts.append(
        f"{mix_ins}amix=inputs={n}:duration=longest:dropout_transition=0:normalize=0,"
        f"loudnorm=I=-16:TP=-1.5:LRA=11,"
        f"apad=pad_dur={out_dur:.2f},atrim=0:{out_dur:.2f},asetpts=PTS-STARTPTS[vo]"
    )
    filter_parts.append(
        f"[1:a]aformat=channel_layouts=stereo,volume=0.12,"
        f"afade=t=in:st=0:d=0.7,afade=t=out:st={out_dur-1.6:.2f}:d=1.5,"
        f"atrim=0:{out_dur:.2f},asetpts=PTS-STARTPTS[bg]"
    )
    filter_parts.append("[vo]asplit=2[vo_key][vo_mix]")
    filter_parts.append(
        "[bg][vo_key]sidechaincompress=threshold=0.05:ratio=7:attack=40:release=280:makeup=1[ducked]"
    )
    filter_parts.append(
        f"[vo_mix]volume=1.35[vox];[ducked][vox]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
        f"atrim=0:{out_dur:.2f}[a]"
    )
    map_v = "0:v"
    vcodec = ["-c:v", "copy"]
    if pad > 0.05:
        filter_parts.append(f"[0:v]tpad=stop_mode=clone:stop_duration={pad:.2f}[v]")
        map_v = "[v]"
        vcodec = ["-c:v", "libx264", "-crf", "18", "-preset", "fast", "-pix_fmt", "yuv420p"]
        print(f"  pad last frame +{pad:.2f}s, out={out_dur:.2f}s")

    fc = ";".join(filter_parts)
    cmd = inputs + [
        "-filter_complex",
        fc,
        "-map",
        map_v,
        "-map",
        "[a]",
        *vcodec,
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-t",
        f"{out_dur:.2f}",
        "-movflags",
        "+faststart",
        str(FINAL),
    ]
    print("ffmpeg assemble...")
    subprocess.run(cmd, check=True)
    return FINAL


def main() -> None:
    VOICE.mkdir(parents=True, exist_ok=True)
    if not PROMPT_WAV.exists():
        sys.exit(f"нет {PROMPT_WAV}")
    prompt_text = PROMPT_TXT.read_text(encoding="utf-8").strip()
    print("prompt_text:", prompt_text)
    for _, text in LINES:
        if len(text) > 200:
            sys.exit(f"строка длиннее 200: {text}")

    client = connect_client()
    clips: list[tuple[float, Path]] = []
    for i, (start, text) in enumerate(LINES, start=1):
        wav = VOICE / "parts" / f"{i:02d}.wav"
        wav.parent.mkdir(parents=True, exist_ok=True)
        stamp = VOICE / "parts" / f"{i:02d}.txt"
        reuse = (
            wav.exists()
            and wav.stat().st_size > 1000
            and stamp.exists()
            and stamp.read_text(encoding="utf-8").strip() == f"{STYLE}\n{text}"
        )
        if reuse:
            print("skip existing", wav)
        else:
            generate_one(client, text, prompt_text, seed=42 + i, out_wav=wav)
            stamp.write_text(f"{STYLE}\n{text}", encoding="utf-8")
        clips.append((start, wav))
    clips = place_clips(clips)
    (VOICE / "script.json").write_text(
        json.dumps(
            [{"t": t, "text": tx, "file": str(p)} for (t, p), (_, tx) in zip(clips, LINES)],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    dur = probe_duration(VIDEO)
    out = assemble(clips, dur)
    print("READY", out, out.stat().st_size)


if __name__ == "__main__":
    main()
