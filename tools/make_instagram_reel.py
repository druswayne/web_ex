"""Снимает вертикальный Reels 9:16 по реальному кабинету ученика."""
from __future__ import annotations

import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WEB_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = WEB_ROOT.parent
sys.path.insert(0, str(WEB_ROOT))
os.chdir(WEB_ROOT)

from app import create_app
from app.models import (
    LessonAccess,
    LessonProgress,
    PracticeAssignment,
    PracticeAttempt,
    TestAttempt,
    User,
    UserAchievement,
    db,
)
from app.progress import bootstrap_student

PORT = 5056
BASE = f"http://127.0.0.1:{PORT}"
USER = "reel"
PASSWORD = "reel123"
OUT_DIR = REPO_ROOT / "web" / "demo_reel"
W, H = 540, 960

def load_test_answers() -> dict[str, list[str]]:
    from app.content import parse_opt_list

    text = (WEB_ROOT / "content" / "lessons" / "01_test_answers.md").read_text(encoding="utf-8")
    out: dict[str, list[str]] = {}
    for m in re.finditer(r"\*\*Т(\d+)\.\*\*\s*([^\n]+)", text):
        out[f"T{int(m.group(1))}"] = parse_opt_list(m.group(2))
    return out

INIT_JS = r"""
(() => {
  if (window.__reelReady) return;
  window.__reelReady = true;
  const style = document.createElement("style");
  style.textContent = `
    #reel-cursor {
      position: fixed; width: 28px; height: 28px; border-radius: 50%;
      border: 3px solid #c97816; background: rgba(201,120,22,.35);
      z-index: 2147483645; pointer-events: none;
      transform: translate(-50%, -50%);
      box-shadow: 0 0 0 6px rgba(201,120,22,.16);
      left: -80px; top: -80px;
    }
    #reel-overlay {
      position: fixed; left: 50%; bottom: 12%;
      transform: translateX(-50%);
      z-index: 2147483645; pointer-events: none;
      background: rgba(13, 110, 106, .94);
      color: #fffdf8;
      font-family: Manrope, system-ui, sans-serif;
      font-weight: 800;
      font-size: 20px;
      line-height: 1.28;
      letter-spacing: -.03em;
      text-align: center;
      padding: 14px 18px;
      border-radius: 18px;
      width: min(90vw, 460px);
      box-shadow: 0 14px 36px rgba(27,42,50,.28);
      opacity: 0;
      transition: opacity .28s ease;
    }
    #reel-overlay.on { opacity: 1; }
  `;
  document.documentElement.appendChild(style);
  const cursor = document.createElement("div");
  cursor.id = "reel-cursor";
  const overlay = document.createElement("div");
  overlay.id = "reel-overlay";
  const mount = () => {
    const root = document.body || document.documentElement;
    if (!root) return;
    if (!cursor.isConnected) root.appendChild(cursor);
    if (!overlay.isConnected) root.appendChild(overlay);
  };
  mount();
  document.addEventListener("DOMContentLoaded", mount);
  document.addEventListener("mousemove", (e) => {
    cursor.style.left = e.clientX + "px";
    cursor.style.top = e.clientY + "px";
  }, true);
  window.__setReelText = (t) => {
    window.__pendingReelText = t || "";
    mount();
    overlay.innerHTML = t || "";
    overlay.classList.toggle("on", Boolean(t));
  };
  if (window.__pendingReelText) window.__setReelText(window.__pendingReelText);
})();
"""

TITLE_HTML = """<!DOCTYPE html>
<html lang="ru"><head>
<meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Manrope:wght@700;800&display=swap" rel="stylesheet">
<style>
  html,body{margin:0;height:100%;}
  body{
    display:flex;align-items:center;justify-content:center;
    background:
      radial-gradient(900px 500px at 20% -10%, #1a9b94 0%, transparent 46%),
      radial-gradient(700px 420px at 110% 80%, #c97816 0%, transparent 42%),
      #0d6e6a;
    color:#fffdf8;font-family:Manrope,system-ui,sans-serif;text-align:center;
  }
  .mark{width:58px;height:58px;border-radius:16px;margin:0 auto 28px;
    background:conic-gradient(from 220deg,#7bc4b8,#fff6e8,#c97816,#0a4f4c);
    box-shadow:inset 0 0 0 4px #fffdf8;}
  h1{font-size:42px;line-height:1.12;font-weight:800;letter-spacing:-.04em;margin:0 28px;}
  p{margin:22px 0 0;opacity:.86;font-weight:700;font-size:16px;}
  .hero{animation:rise .9s ease both;}
  @keyframes rise{from{opacity:0;transform:translateY(22px)}to{opacity:1;transform:none}}
</style></head>
<body>
  <div class="hero">
    <div class="mark"></div>
    <h1>Как мои ученики<br>сами готовятся<br>к экзамену</h1>
    <p>математика · 9 класс</p>
  </div>
</body></html>
"""

END_HTML = """<!DOCTYPE html>
<html lang="ru"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link href="https://fonts.googleapis.com/css2?family=Manrope:wght@700;800&display=swap" rel="stylesheet">
<style>
  html,body{margin:0;width:100%;height:100%;background:#0d6e6a;}
  body{
    min-height:100vh;min-height:100dvh;
    display:flex;align-items:center;justify-content:center;
    color:#fffdf8;font-family:Manrope,Arial,sans-serif;text-align:center;
    background:
      radial-gradient(900px 500px at 20% -10%, #1a9b94 0%, transparent 46%),
      radial-gradient(700px 420px at 110% 80%, #c97816 0%, transparent 42%),
      #0d6e6a;
  }
  .box{padding:28px 32px;animation:rise .9s ease both;}
  h1{font-size:32px;line-height:1.18;font-weight:800;letter-spacing:-.04em;margin:0;}
  p{margin:18px 0 0;opacity:.9;font-weight:700;font-size:17px;line-height:1.35;}
  @keyframes rise{from{opacity:0;transform:translateY(22px)}to{opacity:1;transform:none}}
</style></head>
<body>
  <div class="box">
    <h1>Залог хорошей сдачи —<br>качественная подготовка</h1>
    <p>Практика подстраивается<br>под уровень каждого ученика</p>
  </div>
</body></html>
"""


def reset_reel_user(app) -> None:
    with app.app_context():
        user = User.find_by_username(USER)
        if user is None:
            user = User(
                username=USER,
                full_name="Иван Петров",
                role="student",
                level_band="3-4",
            )
            user.set_password(PASSWORD)
            db.session.add(user)
            db.session.flush()
        else:
            PracticeAttempt.query.filter_by(user_id=user.id).delete()
            PracticeAssignment.query.filter_by(user_id=user.id).delete()
            TestAttempt.query.filter_by(user_id=user.id).delete()
            LessonProgress.query.filter_by(user_id=user.id).delete()
            LessonAccess.query.filter_by(user_id=user.id).delete()
            UserAchievement.query.filter_by(user_id=user.id).delete()
        bootstrap_student(user)
        db.session.commit()


def wait_http(url: str, timeout: float = 90) -> None:
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        try:
            urllib.request.urlopen(url, timeout=3)
            return
        except Exception as exc:
            last = exc
            time.sleep(0.4)
    raise RuntimeError(f"Сервер не ответил: {url} ({last})")


def make_solution_photo(path: Path) -> None:
    img = Image.new("RGB", (1240, 1650), "#f6f0e2")
    draw = ImageDraw.Draw(img)
    for y in range(90, 1600, 52):
        draw.line((48, y, 1190, y), fill="#d7cdba", width=2)
    font_path = Path(r"C:\Windows\Fonts\segoepr.ttf")
    if not font_path.exists():
        font_path = Path(r"C:\Windows\Fonts\comic.ttf")
    if not font_path.exists():
        font_path = Path(r"C:\Windows\Fonts\calibri.ttf")
    font = ImageFont.truetype(str(font_path), 44)
    small = ImageFont.truetype(str(font_path), 36)
    lines = [
        (110, "С1"),
        (190, "3/4 + 1/2 = 3/4 + 2/4 = 5/4"),
        (280, "Ответ: 5/4"),
        (400, "Проверил вычисления."),
    ]
    draw.text((80, lines[0][0]), lines[0][1], font=small, fill="#0d6e6a")
    for y, text in lines[1:]:
        draw.text((80, y), text, font=font, fill="#1b2a32")
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "JPEG", quality=92)


def overlay(page, text: str) -> None:
    page.evaluate(
        """(t) => {
          let el = document.getElementById("reel-overlay");
          if (!el) {
            el = document.createElement("div");
            el.id = "reel-overlay";
            (document.body || document.documentElement).appendChild(el);
            el.style.cssText = `
              position: fixed; left: 50%; bottom: 9%;
              transform: translateX(-50%);
              z-index: 2147483645; pointer-events: none;
              background: rgba(13, 110, 106, .95);
              color: #fffdf8;
              font-family: Manrope, Arial, sans-serif;
              font-weight: 800; font-size: 22px; line-height: 1.28;
              letter-spacing: -.03em; text-align: center;
              padding: 16px 20px; border-radius: 18px;
              width: min(90vw, 460px);
              box-shadow: 0 14px 36px rgba(27,42,50,.28);
              opacity: 0;
              transition: opacity .4s ease;
            `;
          }
          if (!t) {
            el.style.opacity = "0";
            return;
          }
          const shown = parseFloat(getComputedStyle(el).opacity) > 0.4;
          if (el.innerHTML === t && shown) return;
          el.innerHTML = t;
          if (!shown) {
            el.style.opacity = "0";
            el.getBoundingClientRect();
          }
          el.style.opacity = "1";
        }""",
        text,
    )


def hold(page, ms: int, text: str | None = None) -> None:
    if text is not None:
        overlay(page, text)
    page.wait_for_timeout(ms)


def go_scene(page, url: str, wait_sel: str | None = None, caption: str | None = None) -> None:
    page.goto(url.split("#")[0], wait_until="domcontentloaded")
    if wait_sel:
        page.wait_for_selector(wait_sel)
    page.wait_for_timeout(200)
    if caption:
        overlay(page, caption)


def click_scene(page, locator, wait_sel: str | None = None, caption: str | None = None) -> None:
    locator.click()
    if wait_sel:
        page.wait_for_selector(wait_sel)
    page.wait_for_timeout(200)
    if caption:
        overlay(page, caption)


def move_click(page, locator) -> None:
    locator.wait_for(state="visible", timeout=20000)
    box = locator.bounding_box()
    if box:
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=22)
        page.wait_for_timeout(180)
    locator.click()


def type_slow(locator, text: str, delay: int = 90) -> None:
    locator.click()
    locator.fill("")
    locator.type(text, delay=delay)


def slow_scroll(page, to_y: float, duration: int = 2400) -> None:
    page.evaluate(
        """async ({toY, duration}) => {
          const start = window.scrollY;
          const dist = toY - start;
          if (Math.abs(dist) < 4) return;
          const t0 = performance.now();
          await new Promise((resolve) => {
            const step = (now) => {
              const t = Math.min(1, (now - t0) / duration);
              const e = t < 0.5
                ? 4 * t * t * t
                : 1 - Math.pow(-2 * t + 2, 3) / 2;
              window.scrollTo(0, start + dist * e);
              if (t < 1) requestAnimationFrame(step);
              else resolve();
            };
            requestAnimationFrame(step);
          });
        }""",
        {"toY": float(to_y), "duration": duration},
    )


def slow_scroll_el(page, selector: str, duration: int = 1600, block: str = "center") -> None:
    target = page.evaluate(
        """({sel, block}) => {
          const el = document.querySelector(sel);
          if (!el) return window.scrollY;
          const rect = el.getBoundingClientRect();
          const y = window.scrollY;
          if (block === "start") return Math.max(0, y + rect.top - 72);
          if (block === "end") return Math.max(0, y + rect.bottom - window.innerHeight + 28);
          return Math.max(0, y + rect.top - window.innerHeight / 2 + rect.height / 2);
        }""",
        {"sel": selector, "block": block},
    )
    slow_scroll(page, target, duration)


def click_option(page, code: str, letter: str) -> None:
    label = page.locator(f'fieldset[aria-labelledby="qhead-{code}"] label.opt').filter(
        has=page.locator(f'input[value="{letter}"]')
    )
    move_click(page, label.first)


def fill_remaining_answers(page) -> None:
    page.evaluate(
        """(answers) => {
          for (const [code, letters] of Object.entries(answers)) {
            for (const letter of letters) {
              const input = document.querySelector(
                `fieldset[aria-labelledby="qhead-${code}"] input[value="${letter}"]`
              );
              if (input) input.checked = true;
            }
          }
        }""",
        load_test_answers(),
    )


def encode_mp4(webm: Path, mp4: Path) -> None:
    import re
    import subprocess

    import imageio_ffmpeg

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    probe = subprocess.run([ffmpeg, "-i", str(webm)], capture_output=True, text=True)
    info = probe.stderr or ""
    size = re.search(r"(\d{3,4})x(\d{3,4})", info)
    width = int(size.group(1)) if size else 0
    height = int(size.group(2)) if size else 0
    # Playwright пишет CSS-viewport в угол, если record_video_size больше окна.
    if width >= 1000 and height >= 1700:
        vf = "crop=iw/2:ih/2:0:0,scale=1080:1920:flags=lanczos,fps=30,format=yuv420p"
    else:
        vf = "scale=1080:1920:flags=lanczos,fps=30,format=yuv420p"
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(webm),
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-crf",
        "18",
        "-preset",
        "medium",
        "-movflags",
        "+faststart",
        "-an",
        str(mp4),
    ]
    subprocess.check_call(cmd)


def record_reel(photo: Path) -> Path:
    from playwright.sync_api import sync_playwright

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            viewport={"width": W, "height": H},
            device_scale_factor=2,
            record_video_dir=str(OUT_DIR / "raw"),
            record_video_size={"width": W, "height": H},
            locale="ru-RU",
        )
        context.add_init_script(INIT_JS)
        page = context.new_page()
        page.set_default_timeout(25000)

        page.set_content(TITLE_HTML, wait_until="domcontentloaded")
        hold(page, 3200)

        go_scene(page, f"{BASE}/login", "input[name=username]", "Вход по логину<br>от учителя")
        hold(page, 900)
        type_slow(page.locator("input[name=username]"), USER, 110)
        hold(page, 250)
        type_slow(page.locator("input[name=password]"), PASSWORD, 80)
        hold(page, 400)
        click_scene(page, page.locator("button[type=submit]"), ".lesson-grid", "Личный кабинет:<br>прогресс по всему курсу")
        hold(page, 2200)
        slow_scroll_el(page, ".lesson-grid", 1800, "start")
        hold(page, 2200, "35 занятий<br>открываются по мере прохождения")

        tile = page.locator("a.lesson-tile").first
        slow_scroll_el(page, "a.lesson-tile", 1200, "center")
        hold(page, 500)
        click_scene(page, tile, ".path-card", "Каждое занятие —<br>три шага")
        hold(page, 2200)
        slow_scroll_el(page, ".path", 1600, "start")
        hold(page, 2200, "теория · тест · практика")

        click_scene(page, page.locator("a.path-card", has_text="Теория"), ".theory-article", "Теория с прогрессом:<br>дочитал — открылся тест")
        hold(page, 1600)
        y1 = page.evaluate("window.scrollY + window.innerHeight * 0.55")
        slow_scroll(page, y1, 2800)
        hold(page, 600)
        y2 = page.evaluate("window.scrollY + window.innerHeight * 0.7")
        slow_scroll(page, y2, 2600)
        hold(page, 500)
        page.evaluate(
            """async () => {
              const csrf = document.querySelector('meta[name=csrf-token]')?.content || window.csrfToken;
              const url = document.querySelector('.theory-progress')?.dataset.progressUrl;
              if (!url) return;
              await fetch(url, {
                method: 'POST',
                headers: {'Content-Type':'application/json','X-CSRFToken': csrf},
                body: JSON.stringify({pct:100, completed:true, seconds:12})
              });
            }"""
        )
        hold(page, 400)
        go_scene(page, f"{BASE}/lessons/1/test", "form.quiz", "Тест по теории<br>порог — 80%")
        hold(page, 1500)
        answers = load_test_answers()
        for letter in answers.get("T1", []):
            click_option(page, "T1", letter)
            hold(page, 350)
        slow_scroll_el(page, ".q-card:nth-of-type(2)", 1400, "center")
        hold(page, 350)
        for letter in answers.get("T2", []):
            click_option(page, "T2", letter)
            hold(page, 280)
        fill_remaining_answers(page)
        hold(page, 400)
        slow_scroll_el(page, "form.quiz .sticky-actions, form.quiz button[type=submit]", 1600, "end")
        submit = page.locator("form.quiz button[type=submit]")
        click_scene(page, submit, ".result-list, .hero-card h1", "Видно, где ошибка.<br>Правильный ответ скрыт")
        hold(page, 2200)

        practice_btn = page.locator("a.btn-primary", has_text="практической")
        if practice_btn.count() == 0:
            practice_btn = page.locator("a.btn-primary", has_text="практик")
        if practice_btn.count():
            click_scene(page, practice_btn.first, ".paper", "Практика своего уровня:<br>подстраивается под ученика")
        else:
            go_scene(page, f"{BASE}/lessons/1/practice", ".paper", "Практика своего уровня:<br>подстраивается под ученика")
        hold(page, 2400)
        slow_scroll_el(page, ".paper", 1600, "start")
        hold(page, 900)
        slow_scroll_el(page, ".upload-form, .drop, .tips", 1800, "center")
        hold(page, 500, "Решил в тетради —<br>сфотографировал — отправил")
        file_input = page.locator("#photoInput")
        if file_input.count():
            file_input.set_input_files(str(photo))
            hold(page, 1600, "ИИ проверяет фото<br>и пишет, где ошибка")
            send = page.locator("#sendBtn")
            if send.count() and send.is_enabled():
                move_click(page, send)
                page.wait_for_timeout(800)
                overlay(page, "ИИ проверяет фото<br>и пишет, где ошибка")
                page.wait_for_timeout(2800)
        else:
            hold(page, 1800, "ИИ проверяет фото<br>и пишет, где ошибка")

        page.set_content(END_HTML, wait_until="domcontentloaded")
        hold(page, 3400)

        video = page.video
        context.close()
        browser.close()
        webm = Path(video.path())
        return webm


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    photo = OUT_DIR / "solution.jpg"
    make_solution_photo(photo)

    app = create_app()
    reset_reel_user(app)

    def run() -> None:
        app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False, threaded=True)

    threading.Thread(target=run, daemon=True).start()
    wait_http(f"{BASE}/login")

    webm = record_reel(photo)
    mp4 = OUT_DIR / "instagram_demo.mp4"
    encode_mp4(webm, mp4)
    print(f"READY {mp4} size={mp4.stat().st_size}")


if __name__ == "__main__":
    main()
