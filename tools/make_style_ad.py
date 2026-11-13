"""Горизонтальный ролик в ритме рекламы: вопрос, экран курса, призыв.

Кадры чужого ролика, персонаж и бот не используются. Текст не обещает балл.
"""
from __future__ import annotations

import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

from make_instagram_reel import (
    BASE,
    OUT_DIR,
    PORT,
    USER,
    WEB_ROOT,
    freeze_practice_checks,
    login_cookies,
    reset_reel_user,
    seed_check,
    wait_http,
)
from app import create_app
from app.models import User, db
from app.progress import ensure_assignments, get_or_create_progress

AD_DIR = OUT_DIR / "style_ad"
SHOTS = AD_DIR / "shots"
PHOTO = WEB_ROOT / "uploads" / "practice" / "2" / "1" / "20260825_095410_1_orig.jpg"
TOTAL_MS = 51800

HIDE_JS = """() => {
  const style = document.createElement("style");
  style.textContent = `
    footer.site-foot, .who, .cabinet-balance, aside.tips, .to-top,
    .about-jump, .cabinet-title a, .cookie-banner { display: none !important; }
  `;
  document.head.appendChild(style);
}"""


def prepare_progress(app) -> None:
    with app.app_context():
        user = User.find_by_username(USER)
        progress = get_or_create_progress(user.id, 1)
        now = datetime.now(timezone.utc)
        progress.theory_completed_at = now
        progress.test_passed_at = now
        ensure_assignments(user, 1)
        db.session.commit()


def shoot(page, url: str, dest: Path, ready: str, after=None) -> None:
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_selector(ready, timeout=20000)
    page.evaluate(HIDE_JS)
    if after:
        after(page)
    page.wait_for_timeout(400)
    dest.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(dest))
    print(f"shot {dest.name}")


def capture(cookies: list[dict], app, photo: Path) -> int:
    task_id = seed_check(app, photo)
    if not task_id:
        raise RuntimeError("Не удалось подготовить проверку практики")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 420, "height": 905},
            device_scale_factor=2,
        )
        context.add_cookies(
            [*cookies, {"name": "cookie_consent", "value": "accepted", "url": BASE + "/"}]
        )
        page = context.new_page()

        def tiles(pg):
            pg.evaluate(
                """() => {
                  const el = document.querySelector('.lesson-grid');
                  const top = el.getBoundingClientRect().top + window.scrollY - 18;
                  window.scrollTo(0, Math.max(0, top));
                }"""
            )

        def path(pg):
            pg.evaluate(
                """() => {
                  document.querySelectorAll('.goals-card, .hero-card, .crumb').forEach((el) => {
                    el.style.display = 'none';
                  });
                  const el = document.querySelector('.path');
                  const top = el.getBoundingClientRect().top + window.scrollY - 8;
                  window.scrollTo(0, Math.max(0, top));
                }"""
            )

        def theory(pg):
            pg.locator("#chooseText").click()
            pg.wait_for_selector("#theoryTextCard:not([hidden]) .theory-article")
            pg.wait_for_timeout(300)

        def practice(pg):
            pg.wait_for_selector(".ai-feedback")
            pg.evaluate(
                """() => {
                  const el = document.querySelector(".ai-feedback");
                  if (!el || el.previousElementSibling?.classList.contains("reel-agent-label")) return;
                  const label = document.createElement("p");
                  label.className = "reel-agent-label";
                  label.textContent = "Комментарий агента по итогам проверки";
                  label.style.cssText = "margin:14px 0 8px;font:800 16px/1.3 Manrope,sans-serif;color:#0d6e6a;";
                  el.before(label);
                  label.scrollIntoView({block: "center"});
                }"""
            )

        shoot(page, f"{BASE}/cabinet", SHOTS / "cabinet.png", ".cabinet-title")
        shoot(page, f"{BASE}/cabinet", SHOTS / "tiles.png", ".lesson-grid", tiles)
        shoot(page, f"{BASE}/lessons/1", SHOTS / "path.png", ".path", path)
        shoot(page, f"{BASE}/lessons/1/theory", SHOTS / "theory.png", ".theory-hero h1", theory)
        shoot(
            page,
            f"{BASE}/lessons/1/practice?task={task_id}",
            SHOTS / "check.png",
            ".ai-feedback",
            practice,
        )
        browser.close()
    return task_id


def ad_html() -> str:
    return r"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<style>
  * { box-sizing: border-box; }
  html, body { margin: 0; width: 1280px; height: 720px; overflow: hidden; background: #1c1917; }
  body { font-family: "Segoe UI", sans-serif; color: #2c3344; }
  .scene {
    position: absolute; inset: 0; opacity: 0;
    animation: hold 6s linear both;
  }
  @keyframes hold {
    0% { opacity: 0; }
    7% { opacity: 1; }
    90% { opacity: 1; }
    100% { opacity: 0; }
  }
  .s1 { animation-duration: 5.6s; }
  .s2 { animation-delay: 5.15s; animation-duration: 5.7s; }
  .s3 { animation-delay: 10.4s; animation-duration: 6.2s; }
  .s4 { animation-delay: 16.15s; animation-duration: 7.2s; }
  .s5 { animation-delay: 22.9s; animation-duration: 7.6s; }
  .s6 { animation-delay: 30.05s; animation-duration: 7.8s; }
  .s7 { animation-delay: 37.4s; animation-duration: 7.6s; }
  .s8 {
    animation-name: enter; animation-delay: 44.55s; animation-duration: .5s;
  }
  @keyframes enter { from { opacity: 0; } to { opacity: 1; } }

  .cam { position: absolute; inset: 0; overflow: hidden; }
  .cam > * { position: absolute; }
  .grade { position: absolute; inset: 0; pointer-events: none;
    background: radial-gradient(120% 90% at 50% 40%, transparent 40%, rgba(0,0,0,.38) 100%);
    mix-blend-mode: multiply; }
  .live { position: absolute; inset: 0; overflow: hidden; background: #1c1917; }
  .live video {
    position: absolute; inset: 0; width: 100%; height: 100%;
    object-fit: cover; filter: saturate(.7) contrast(1.04);
  }
  .pill, .grade { z-index: 2; }
  .pill {
    position: absolute; left: 50%; bottom: 54px; transform: translateX(-50%);
    width: min(78%, 860px); margin: 0; text-align: center;
    background: rgba(255, 250, 244, .94); color: #3c4460;
    font-size: 28px; font-weight: 560; line-height: 1.35; letter-spacing: -.02em;
    padding: 16px 28px; border-radius: 18px;
    box-shadow: 0 10px 28px rgba(20, 16, 12, .16);
  }
  .drift { animation: drift 8s ease-out both; }
  @keyframes drift { from { transform: scale(1.08); } to { transform: scale(1); } }

  .room { background: #241f1c; }
  .room .wall { inset: 0; background: linear-gradient(180deg, #4a4038 0%, #2a241f 42%, #1a1614 100%); }
  .room .win { left: 150px; top: 36px; width: 250px; height: 300px; border-radius: 4px;
    background: linear-gradient(180deg, #f3ecdf, #c9bfae 70%, #a89884);
    box-shadow: 0 0 80px 20px rgba(243, 230, 210, .25); }
  .room .mullion { left: 268px; top: 36px; width: 8px; height: 300px; background: #6d645c; }
  .room .rail { left: 150px; top: 180px; width: 250px; height: 8px; background: #6d645c; }
  .room .bed { left: 80px; right: 60px; bottom: -30px; height: 290px; border-radius: 80px 80px 0 0;
    background: linear-gradient(180deg, #5c5148, #3d342e); }
  .room .pillow { left: 150px; bottom: 150px; width: 210px; height: 90px; border-radius: 40px;
    background: #6e6258; }
  .room .device { left: 470px; bottom: 150px; width: 78px; height: 132px; border-radius: 12px;
    background: #111; box-shadow: 0 0 24px rgba(120, 160, 170, .35); }
  .room .screen { left: 478px; bottom: 164px; width: 62px; height: 100px; border-radius: 4px;
    background: linear-gradient(160deg, #8aa4a8, #314248); }

  .desk { background: #d5cec3; }
  .desk .wall { inset: 0; background: linear-gradient(180deg, #ddd6cb 0%, #cfc6b8 58%, #b7aa9a 58%, #a89886 100%); }
  .desk .win { left: 430px; top: 28px; width: 280px; height: 250px;
    background: linear-gradient(180deg, #f7f1e6, #d9d0c2); box-shadow: 0 0 40px rgba(255,248,236,.8); }
  .desk .table { left: 0; right: 0; bottom: 0; height: 250px; background: linear-gradient(180deg, #8d7562, #6e5b4c); }
  .desk .book { bottom: 210px; width: 46px; border-radius: 3px 3px 0 0; }
  .desk .b1 { left: 180px; height: 150px; background: #6e7c74; }
  .desk .b2 { left: 232px; height: 170px; background: #8d6d62; }
  .desk .b3 { left: 284px; height: 140px; background: #6a6270; }
  .desk .glass { left: 430px; bottom: 230px; width: 54px; height: 90px; border-radius: 8px 8px 16px 16px;
    background: linear-gradient(180deg, rgba(255,255,255,.55), rgba(190,210,216,.45)); border: 2px solid rgba(255,255,255,.7); }
  .desk .note { left: 620px; bottom: 160px; width: 280px; height: 180px; border-radius: 6px;
    background: #f4efe4; transform: rotate(-6deg);
    box-shadow: 0 12px 24px rgba(40,30,20,.18); }
  .desk .line { left: 648px; height: 2px; background: #d9d0c2; }
  .desk .l1 { bottom: 290px; width: 180px; }
  .desk .l2 { bottom: 260px; width: 200px; }
  .desk .l3 { bottom: 230px; width: 150px; }

  .board { background: #c9c3b6; }
  .board .wall { inset: 0; background: linear-gradient(180deg, #d4cec2, #c3bcae 62%, #b3aa9c 62%); }
  .board .slate { left: 160px; top: 36px; width: 960px; height: 390px; border-radius: 8px;
    background: #2c3831; box-shadow: inset 0 0 0 10px #4d463c, 0 16px 30px rgba(0,0,0,.18); }
  .board .chalk { color: rgba(236, 232, 220, .88); font-family: "Segoe Print", "Segoe Script", cursive;
    font-size: 34px; }
  .board .c1 { left: 230px; top: 80px; }
  .board .c2 { left: 230px; top: 150px; }
  .board .c3 { left: 620px; top: 110px; }
  .board .c4 { left: 620px; top: 190px; }
  .board .deskrow { left: 80px; bottom: 36px; width: 180px; height: 90px; border-radius: 8px 8px 0 0; background: #8d7b68; }
  .board .d2 { left: 320px; }
  .board .d3 { left: 560px; }
  .board .d4 { left: 800px; }
  .board .d5 { left: 1020px; }

  .paper { background: #efeae1; }
  .layout, .welcome-grid, .end-grid {
    height: 100%; display: grid; align-items: center;
  }
  .layout { grid-template-columns: 1.15fr 300px 150px; padding: 0 48px 0 72px; gap: 12px; }
  .welcome-grid { grid-template-columns: 1fr 320px; padding: 0 90px 0 86px; }
  .end-grid { grid-template-columns: 1.1fr 340px 160px; padding: 0 56px 0 86px; }
  h2 {
    margin: 0; font-weight: 560; font-size: 52px; line-height: 1.16;
    letter-spacing: -.035em; color: #2a3142;
  }
  .welcome-grid h2 { font-size: 58px; }
  .welcome-grid h2 em { font-style: normal; font-weight: 720; }
  .mark {
    width: 74px; height: 74px; border-radius: 22px; margin-bottom: 28px;
    background: conic-gradient(from 220deg, #0d6e6a, #7bc4b8, #e0a15a, #0d6e6a);
    box-shadow: inset 0 0 0 5px #fff;
  }
  .side { display: flex; align-items: flex-end; height: 520px; justify-content: center; }
  .side .mark { width: 92px; height: 92px; border-radius: 28px; margin: 0 0 18px; }
  .phone {
    width: 280px; height: 580px; border-radius: 36px; background: #111;
    border: 10px solid #1b1b1b; overflow: hidden;
    box-shadow: 0 28px 50px rgba(40, 32, 24, .18);
  }
  .phone img { width: 100%; height: 100%; object-fit: contain; object-position: top center; display: block; background: #f6f1e8; }
  .card {
    background: #fff; border-radius: 28px; padding: 28px 22px 32px;
    text-align: center; box-shadow: 0 18px 40px rgba(40, 32, 24, .1);
  }
  .card .mark { margin: 0 auto 18px; }
  .card strong { display: block; font-size: 28px; letter-spacing: -.03em; }
  .card span { display: block; margin-top: 8px; color: #667084; font-size: 18px; }
  .dots { position: relative; height: 220px; }
  .dots i {
    position: absolute; width: 10px; height: 14px; border-radius: 3px; display: block;
  }
  .dots i:nth-child(1) { left: 20px; top: 30px; background: #e07a5f; transform: rotate(20deg); }
  .dots i:nth-child(2) { left: 70px; top: 10px; background: #0d6e6a; transform: rotate(-15deg); }
  .dots i:nth-child(3) { left: 110px; top: 46px; background: #e0a15a; transform: rotate(30deg); }
  .dots i:nth-child(4) { left: 40px; top: 80px; background: #7bc4b8; }
  .dots i:nth-child(5) { left: 96px; top: 96px; background: #c06c84; transform: rotate(-25deg); }
  .dots i:nth-child(6) { left: 18px; top: 130px; background: #e0a15a; transform: rotate(12deg); }
</style>
</head>
<body>
  <section class="scene s1">
    <div class="live"><video src="stock/23382-720.mp4" muted playsinline loop></video></div>
    <div class="grade"></div>
    <p class="pill">Много тем, и непонятно, с чего начать?</p>
  </section>
  <section class="scene s2">
    <div class="live"><video src="stock/45148-720.mp4" muted playsinline loop></video></div>
    <div class="grade"></div>
    <p class="pill">Не хочется оставлять программу на последний момент?</p>
  </section>
  <section class="scene s3">
    <div class="live"><video src="stock/28315-1080.mp4" muted playsinline loop></video></div>
    <div class="grade"></div>
    <p class="pill">Ищете спокойный способ готовиться в своём темпе?</p>
  </section>
  <section class="scene paper s4">
    <div class="welcome-grid">
      <div>
        <div class="mark"></div>
        <h2>Добро пожаловать<br>в курс<br><em>Математика 9</em></h2>
      </div>
      <div class="phone"><img src="shots/cabinet.png" alt=""></div>
    </div>
  </section>
  <section class="scene paper s5">
    <div class="layout">
      <h2>• 35 занятий<br>по порядку</h2>
      <div class="phone"><img src="shots/tiles.png" alt=""></div>
      <div class="side"><div class="mark"></div></div>
    </div>
  </section>
  <section class="scene paper s6">
    <div class="layout">
      <h2>• Теория, тест<br>и практика<br>в тетради</h2>
      <div class="phone"><img src="shots/path.png" alt=""></div>
      <div class="side"><div class="mark"></div></div>
    </div>
  </section>
  <section class="scene paper s7">
    <div class="layout">
      <h2>• Помощник<br>проверяет фото<br>решения</h2>
      <div class="phone"><img src="shots/check.png" alt=""></div>
      <div class="side"><div class="mark"></div></div>
    </div>
  </section>
  <section class="scene paper s8">
    <div class="end-grid">
      <h2>И ещё вся<br>программа<br>девятого класса.<br>Посмотри курс<br>на сайте.</h2>
      <div class="card">
        <div class="mark"></div>
        <strong>Математика 9</strong>
        <span>35 занятий · свой темп</span>
      </div>
      <div class="dots"><i></i><i></i><i></i><i></i><i></i><i></i></div>
    </div>
  </section>
</body>
</html>
"""


def record() -> Path:
    html_path = AD_DIR / "index.html"
    html_path.write_text(ad_html(), encoding="utf-8")
    raw = AD_DIR / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--autoplay-policy=no-user-gesture-required", "--allow-file-access-from-files"],
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 720},
            record_video_dir=str(raw),
            record_video_size={"width": 1280, "height": 720},
        )
        page = context.new_page()
        page.goto(html_path.as_uri())
        page.wait_for_function(
            "() => [...document.querySelectorAll('video')].every(v => v.readyState >= 2)",
            timeout=20000,
        )
        page.evaluate(
            """() => document.querySelectorAll('video').forEach(v => { v.muted = true; v.play(); })"""
        )
        page.wait_for_timeout(TOTAL_MS)
        video = page.video
        context.close()
        browser.close()
        return Path(video.path())


def encode(webm: Path) -> Path:
    import subprocess

    import imageio_ffmpeg

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    mp4 = OUT_DIR / "math9_style_ad.mp4"
    subprocess.check_call(
        [
            ffmpeg, "-y", "-i", str(webm),
            "-vf", "scale=1920:1080:flags=lanczos,fps=30,format=yuv420p",
            "-c:v", "libx264", "-crf", "18", "-preset", "medium",
            "-movflags", "+faststart", "-an", str(mp4),
        ]
    )
    frames = AD_DIR / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    for sec in (2, 7, 13, 19, 26, 33, 41, 48):
        subprocess.check_call(
            [
                ffmpeg, "-y", "-ss", str(sec), "-i", str(mp4),
                "-frames:v", "1", str(frames / f"t{sec:02d}.png"),
            ]
        )
    return mp4


def main() -> None:
    if not PHOTO.exists():
        raise SystemExit(f"Нет фото решения: {PHOTO}")
    AD_DIR.mkdir(parents=True, exist_ok=True)
    shots_ready = all(
        (SHOTS / name).exists()
        for name in ("cabinet.png", "tiles.png", "path.png", "check.png")
    )
    if not shots_ready:
        freeze_practice_checks()
        app = create_app()
        reset_reel_user(app)
        prepare_progress(app)

        def run() -> None:
            app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False, threaded=True)

        threading.Thread(target=run, daemon=True).start()
        wait_http(f"{BASE}/login")
        cookies = login_cookies()
        capture(cookies, app, PHOTO)
    webm = record()
    mp4 = encode(webm)
    print(f"READY {mp4} size={mp4.stat().st_size}")


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    main()
