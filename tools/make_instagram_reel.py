"""Снимает вертикальный Reels 9:16 по реальному кабинету ученика."""
from __future__ import annotations

import html
import http.cookiejar
import json
import os
import re
import shutil
import sys
import threading
import time
from decimal import Decimal
import urllib.parse
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
    TheoryChatMessage,
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
      position: fixed;
      left: 50%;
      bottom: 30%;
      transform: translateX(-50%);
      z-index: 2147483645; pointer-events: none;
      background: rgba(13, 110, 106, .94);
      color: #fffdf8;
      font-family: Manrope, system-ui, sans-serif;
      font-weight: 800;
      font-size: 22px;
      line-height: 1.25;
      letter-spacing: -.03em;
      text-align: center;
      padding: 12px 14px;
      border-radius: 16px;
      width: min(76vw, 420px);
      box-shadow: 0 14px 36px rgba(27,42,50,.28);
      opacity: 0;
      transition: opacity .28s ease;
    }
    #reel-overlay.on { opacity: 1; }
    #reel-fade {
      position: fixed; inset: 0; z-index: 2147483646;
      background: #f3eee4; pointer-events: none; opacity: 1;
    }
    footer.site-foot,
    .who,
    .cabinet-balance,
    aside.tips,
    .to-top,
    .about-jump,
    .result-list { display: none !important; }
    #method .about-lead,
    #method .about-step p { display: none !important; }
  `;
  document.documentElement.appendChild(style);
  const cursor = document.createElement("div");
  cursor.id = "reel-cursor";
  const overlay = document.createElement("div");
  overlay.id = "reel-overlay";
  const fade = document.createElement("div");
  fade.id = "reel-fade";
  const mount = () => {
    const root = document.body || document.documentElement;
    if (!root) return;
    if (!cursor.isConnected) root.appendChild(cursor);
    if (!overlay.isConnected) root.appendChild(overlay);
    if (!fade.isConnected) root.appendChild(fade);
  };
  mount();
  document.addEventListener("DOMContentLoaded", mount);
  document.addEventListener("mousemove", (e) => {
    cursor.style.left = e.clientX + "px";
    cursor.style.top = e.clientY + "px";
  }, true);
  const hideChrome = () => {
    document.querySelectorAll(
      "footer.site-foot, .who, .cabinet-balance, aside.tips, .to-top, .about-jump, .result-list"
    ).forEach((el) => el.style.setProperty("display", "none", "important"));
  };
  hideChrome();
  document.addEventListener("DOMContentLoaded", hideChrome);
  window.__reelReveal = () => {
    hideChrome();
    fade.style.transition = "opacity .5s ease";
    fade.style.opacity = "0";
  };
  window.__setReelText = (t) => {
    window.__pendingReelText = t || "";
    mount();
    overlay.innerHTML = t || "";
    overlay.classList.toggle("on", Boolean(t));
  };
  if (window.__pendingReelText) window.__setReelText(window.__pendingReelText);
})();
"""

def phrase_card(title: str, note: str = "", kicker: str = "") -> str:
    kick = f'<p class="kick">{kicker}</p>' if kicker else ""
    sub = f'<p class="note">{note}</p>' if note else ""
    return f"""<!DOCTYPE html>
<html lang="ru"><head>
<meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Manrope:wght@700;800&display=swap" rel="stylesheet">
<style>
  html,body{{margin:0;height:100%;}}
  body{{
    display:flex;align-items:center;justify-content:center;
    box-sizing:border-box;
    padding:14vh 12% 26vh 12%;
    background:
      radial-gradient(900px 500px at 18% -10%, #1a9b94 0%, transparent 46%),
      radial-gradient(700px 420px at 120% 100%, #c97816 0%, transparent 42%),
      #0d6e6a;
    color:#fffdf8;font-family:Manrope,Segoe UI,Arial,sans-serif;text-align:center;
  }}
  .hero{{width:100%;transition:opacity .45s ease;}}
  .kick{{margin:0 0 12px;font-size:15px;font-weight:800;letter-spacing:.06em;text-transform:uppercase;opacity:.82;}}
  h1{{font-size:34px;line-height:1.16;font-weight:800;letter-spacing:-.04em;margin:0;}}
  .note{{margin:16px 0 0;font-size:18px;font-weight:700;line-height:1.35;opacity:.92;}}
</style></head>
<body><div class="hero">{kick}<h1>{title}</h1>{sub}</div></body></html>
"""


OPEN_CARDS = (
    phrase_card("Много тем,<br>формул и задач", "Знакомо?", "Девятый класс"),
    phrase_card("Экзамен в 9 классе —<br>это волнительно"),
    phrase_card(
        "Но к нему можно<br>подготовиться заранее",
        "По порядку, без гонки в последний момент",
    ),
)
END_HTML = phrase_card(
    "Не оставляй всю программу<br>на последний момент",
    "35 занятий по порядку.<br>Посмотри курс на сайте",
)


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
            TheoryChatMessage.query.filter_by(user_id=user.id).delete()
        bootstrap_student(user)
        user.balance_byn = Decimal("5.00")
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
              position: fixed; left: 50%; bottom: 30%;
              transform: translateX(-50%);
              z-index: 2147483645; pointer-events: none;
              background: rgba(13, 110, 106, .95);
              color: #fffdf8;
              font-family: Manrope, Arial, sans-serif;
              font-weight: 800; font-size: 22px; line-height: 1.25;
              letter-spacing: -.03em; text-align: center;
              padding: 12px 14px; border-radius: 16px;
              width: min(76vw, 420px);
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


def fade_out(page) -> None:
    page.evaluate(
        """() => {
          const el = document.getElementById("reel-fade");
          if (!el) return;
          el.style.transition = "opacity .42s ease";
          el.style.opacity = "1";
        }"""
    )
    page.wait_for_timeout(460)


REEL_CHROME_CSS = """
footer.site-foot,
.who,
.cabinet-balance,
aside.tips,
.to-top,
.about-jump,
.goals-card,
.result-list,
.result-list + p {
  display: none !important;
}
#method .about-lead,
#method .about-step p { display: none !important; }
#method, .path, .upload-form, .paper, .attempt-feed { padding-bottom: 46vh; }
"""


def ensure_chrome(page) -> None:
    page.evaluate(
        """(css) => {
          let style = document.getElementById("reel-chrome");
          if (!style) {
            style = document.createElement("style");
            style.id = "reel-chrome";
            (document.head || document.documentElement).appendChild(style);
          }
          style.textContent = css;
        }""",
        REEL_CHROME_CSS,
    )


def fade_in(page) -> None:
    ensure_chrome(page)
    page.evaluate("() => { if (window.__reelReveal) window.__reelReveal(); }")
    page.wait_for_timeout(520)


def crossfade_card(page, title: str, note: str = "", kicker: str = "") -> None:
    page.evaluate(
        """({title, note, kicker}) => {
          const hero = document.querySelector(".hero");
          if (!hero) return;
          const apply = () => {
            const kick = kicker ? `<p class="kick">${kicker}</p>` : "";
            const sub = note ? `<p class="note">${note}</p>` : "";
            hero.innerHTML = `${kick}<h1>${title}</h1>${sub}`;
            requestAnimationFrame(() => { hero.style.opacity = "1"; });
          };
          hero.style.transition = "opacity .45s ease";
          hero.style.opacity = "0";
          window.setTimeout(apply, 460);
        }""",
        {"title": title, "note": note, "kicker": kicker},
    )
    page.wait_for_timeout(980)


def go_scene(page, url: str, wait_sel: str | None = None, caption: str | None = None) -> None:
    fade_out(page)
    page.goto(url.split("#")[0], wait_until="domcontentloaded")
    if wait_sel:
        page.wait_for_selector(wait_sel)
    fade_in(page)
    if caption:
        overlay(page, caption)


def click_scene(
    page,
    locator,
    wait_sel: str | None = None,
    caption: str | None = None,
    *,
    fade: bool = True,
) -> None:
    if fade:
        fade_out(page)
    locator.click()
    if wait_sel:
        page.wait_for_selector(wait_sel)
    if fade:
        fade_in(page)
    else:
        page.wait_for_timeout(280)
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


def pin_tutor_answer(page) -> None:
    page.evaluate(
        """() => {
          const log = document.querySelector("#tutorLog");
          const msg = log && [...log.querySelectorAll(".tutor-msg")].pop();
          if (!log || !msg) return;
          const limit = window.innerHeight * 0.50;
          const rect = msg.getBoundingClientRect();
          if (rect.bottom > limit) log.scrollTop += rect.bottom - limit;
        }"""
    )


def scroll_bottom_clear(page, selector: str, duration: int = 1100) -> None:
    """Ставит низ элемента над плашкой субтитров."""
    target = page.evaluate(
        """(sel) => {
          const el = document.querySelector(sel);
          if (!el) return window.scrollY;
          const rect = el.getBoundingClientRect();
          const limit = window.innerHeight * 0.56;
          return Math.max(0, window.scrollY + rect.bottom - limit);
        }""",
        selector,
    )
    slow_scroll(page, target, duration)


def scroll_top_ratio(page, selector: str, ratio: float, duration: int = 800) -> None:
    """Ставит верх элемента на долю высоты экрана."""
    target = page.evaluate(
        """({sel, ratio}) => {
          const el = document.querySelector(sel);
          if (!el) return window.scrollY;
          const rect = el.getBoundingClientRect();
          return Math.max(0, window.scrollY + rect.top - window.innerHeight * ratio);
        }""",
        {"sel": selector, "ratio": ratio},
    )
    slow_scroll(page, target, duration)


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


def login_cookies() -> list[dict]:
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    html_page = opener.open(f"{BASE}/login", timeout=15).read().decode("utf-8", "replace")
    match = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html_page)
    if not match:
        match = re.search(r'value="([^"]+)"[^>]*name="csrf_token"', html_page)
    token = match.group(1) if match else ""
    data = urllib.parse.urlencode(
        {"username": USER, "password": PASSWORD, "csrf_token": token}
    ).encode()
    opener.open(urllib.request.Request(f"{BASE}/login", data=data, method="POST"), timeout=15)
    return [{"name": c.name, "value": c.value, "url": BASE + "/"} for c in jar]


def freeze_practice_checks() -> None:
    """Ролик показывает экран ожидания и не запускает настоящую проверку."""
    import app.check_jobs as check_jobs

    def _noop(*_args, **_kwargs):
        return None

    check_jobs.start_practice_check = _noop
    check_jobs.resume_pending_practice = _noop
    check_jobs.sweep_stale_pending = _noop


def _reel_assignment(user: User):
    return (
        PracticeAssignment.query.filter_by(user_id=user.id, lesson_number=1, is_active=True)
        .order_by(PracticeAssignment.slot.asc())
        .first()
    )


def seed_pending(app, photo: Path) -> int | None:
    from app.config import UPLOADS_DIR

    with app.app_context():
        user = User.find_by_username(USER)
        if user is None:
            return None
        assignment = _reel_assignment(user)
        if assignment is None:
            return None
        pending = (
            PracticeAttempt.query.filter_by(assignment_id=assignment.id)
            .filter(PracticeAttempt.ai_verdict.in_(("pending", "running")))
            .first()
        )
        if pending is not None:
            return assignment.id
        folder = UPLOADS_DIR / "practice" / str(user.id) / str(assignment.id)
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / "solution.jpg"
        shutil.copyfile(photo, dest)
        rel = f"practice/{user.id}/{assignment.id}/solution.jpg"
        prev = (
            PracticeAttempt.query.filter_by(assignment_id=assignment.id)
            .order_by(PracticeAttempt.attempt_no.desc())
            .first()
        )
        db.session.add(
            PracticeAttempt(
                assignment_id=assignment.id,
                user_id=user.id,
                attempt_no=(prev.attempt_no + 1) if prev else 1,
                photo_filename=rel,
                processed_filename=rel,
                is_correct=False,
                ai_verdict="pending",
                ai_feedback="Проверяем решение…",
            )
        )
        db.session.commit()
        return assignment.id


def seed_check(app, photo: Path) -> int | None:
    from app.config import UPLOADS_DIR
    from app.progress import refresh_practice_progress

    feedback = "Выражение больше. Агент сверил шаги по фото решения."
    with app.app_context():
        user = User.find_by_username(USER)
        if user is None:
            return None
        assignment = _reel_assignment(user)
        if assignment is None:
            return None
        rows = (
            PracticeAttempt.query.filter_by(assignment_id=assignment.id)
            .order_by(PracticeAttempt.attempt_no.asc())
            .all()
        )
        assignment.is_correct = True
        if rows:
            for extra in rows[:-1]:
                db.session.delete(extra)
            last = rows[-1]
            last.is_correct = True
            last.ai_verdict = "correct"
            last.ai_feedback = feedback
        else:
            folder = UPLOADS_DIR / "practice" / str(user.id) / str(assignment.id)
            folder.mkdir(parents=True, exist_ok=True)
            dest = folder / "solution.jpg"
            shutil.copyfile(photo, dest)
            rel = f"practice/{user.id}/{assignment.id}/solution.jpg"
            db.session.add(
                PracticeAttempt(
                    assignment_id=assignment.id,
                    user_id=user.id,
                    attempt_no=1,
                    photo_filename=rel,
                    processed_filename=rel,
                    is_correct=True,
                    ai_verdict="correct",
                    ai_feedback=feedback,
                )
            )
        refresh_practice_progress(user, 1)
        db.session.commit()
        return assignment.id


def show_pending_message(page, app, photo: Path) -> None:
    showed = False
    send = page.locator("#sendBtn")
    if send.count():
        try:
            overlay(page, "")
            slow_scroll_el(page, "#sendBtn", 900, "center")
            page.wait_for_function(
                "() => { const b = document.getElementById('sendBtn'); return !!b && !b.disabled; }",
                timeout=8000,
            )
            hold(page, 450)
            move_click(page, send)
            page.wait_for_selector("#pendingCard", timeout=25000)
            try:
                page.wait_for_function(
                    """() => {
                      const paper = document.querySelector('.paper');
                      const card = document.getElementById('pendingCard');
                      return !!paper && !!card && !paper.innerText.includes('$$') && !!paper.querySelector('.katex');
                    }""",
                    timeout=8000,
                )
            except Exception:
                pass
            ensure_chrome(page)
            page.wait_for_timeout(250)
            scroll_top_ratio(page, "#pendingCard", 0.1, 500)
            page.wait_for_timeout(200)
            scroll_top_ratio(page, "#pendingCard", 0.1, 200)
            fade_in(page)
            showed = True
        except Exception as exc:
            print(f"submit scene fallback: {exc}")
    if not showed:
        task_id = seed_pending(app, photo)
        url = f"{BASE}/lessons/1/practice"
        if task_id:
            url += f"?task={task_id}"
        go_scene(page, url, "#pendingCard")
        scroll_top_ratio(page, "#pendingCard", 0.12, 500)
    hold(page, 3200, "Пока проверяется —<br>можно решать другие")


CHAT_ANSWER_ASK = (
    "<p>Рациональное число можно записать дробью: числитель целый, знаменатель натуральный.</p>"
    "<p>Например, 0,25 — это 25/100. Число −7 тоже рациональное, но не натуральное.</p>"
)
CHAT_ANSWER_QUOTE = (
    "<p>Здесь про натуральные числа: 1, 2, 3 и дальше, когда считают предметы.</p>"
    "<p>Ноль и отрицательные числа в этот ряд не входят.</p>"
)


def install_chat_mock(context) -> None:
    state = {"n": 1, "msgs": [], "ready_at": 0.0, "answer": ""}

    def answer_for(question: str) -> str:
        q = (question or "").lower()
        if "фрагмент" in q or "счёте предметов" in q or "счете предметов" in q:
            return CHAT_ANSWER_QUOTE
        return CHAT_ANSWER_ASK

    def handle(route) -> None:
        req = route.request
        if req.method == "GET":
            msgs = list(state["msgs"])
            if msgs and msgs[-1]["role"] == "assistant" and msgs[-1]["pending"]:
                if time.time() >= state["ready_at"]:
                    msgs[-1]["pending"] = False
                    msgs[-1]["html"] = state["answer"]
                    state["msgs"] = msgs
            route.fulfill(
                status=200,
                content_type="application/json; charset=utf-8",
                body=json.dumps(
                    {"ok": True, "messages": state["msgs"], "balance": "5,00 Br"},
                    ensure_ascii=False,
                ),
            )
            return
        if req.method == "POST":
            data = req.post_data_json or {}
            question = str(data.get("message") or "")
            uid, aid = state["n"], state["n"] + 1
            state["n"] += 2
            escaped = html.escape(question).replace("\n", "<br>\n")
            user = {"id": uid, "role": "user", "html": f"<p>{escaped}</p>", "pending": False}
            bot = {"id": aid, "role": "assistant", "html": "<p>Думаю…</p>", "pending": True}
            state["msgs"].extend([user, bot])
            state["ready_at"] = time.time() + 1.2
            state["answer"] = answer_for(question)
            route.fulfill(
                status=200,
                content_type="application/json; charset=utf-8",
                body=json.dumps(
                    {"ok": True, "pending": True, "user": user, "assistant": bot, "balance": "5,00 Br"},
                    ensure_ascii=False,
                ),
            )
            return
        route.fallback()

    context.route("**/lessons/*/theory/chat", handle)


def wait_tutor_answer(page, timeout_ms: int = 15000) -> None:
    page.wait_for_function(
        """() => {
          const rows = [...document.querySelectorAll('.tutor-msg-assistant')];
          const last = rows[rows.length - 1];
          if (!last || last.classList.contains('is-typing')) return false;
          const t = (last.innerText || '').trim();
          return t.length > 20 && !t.startsWith('Думаю');
        }""",
        timeout=timeout_ms,
    )


def close_tutor(page) -> None:
    btn = page.locator(".tutor-sheet [data-tutor-close]").last
    if btn.count():
        move_click(page, btn)
        page.wait_for_timeout(350)


def select_theory_fragment(page, needle: str) -> None:
    loc = page.locator(".theory-article p").filter(has_text=needle).first
    loc.wait_for(state="visible")
    box = loc.bounding_box()
    if not box:
        raise RuntimeError("не найден фрагмент теории")
    y = box["y"] + min(20, max(8, box["height"] / 3))
    page.mouse.move(box["x"] + 8, y, steps=14)
    page.wait_for_timeout(140)
    page.mouse.down()
    page.mouse.move(box["x"] + min(box["width"] - 12, 260), y, steps=26)
    page.mouse.up()
    page.wait_for_selector("#tutorAskSel:not([hidden])", timeout=5000)


def show_tutor(page) -> None:
    page.evaluate(
        """() => {
          const root = document.querySelector('.theory-article');
          if (!root) return;
          const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
          let node;
          while ((node = walker.nextNode())) {
            if ((node.textContent || '').includes('при счёте предметов')) {
              const el = node.parentElement;
              const y = el.getBoundingClientRect().top + window.scrollY - window.innerHeight * 0.22;
              window.scrollTo(0, Math.max(0, y));
              return;
            }
          }
        }"""
    )
    page.wait_for_timeout(400)


def mark_theory_done(page) -> None:
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


def record_reel(photo: Path, cookies: list[dict], app) -> Path:
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
        install_chat_mock(context)
        context.add_cookies(
            [{"name": "cookie_consent", "value": "accepted", "url": BASE + "/"}]
        )
        page = context.new_page()
        page.set_default_timeout(25000)

        page.set_content(OPEN_CARDS[0], wait_until="domcontentloaded")
        fade_in(page)
        hold(page, 2800)
        crossfade_card(page, "Экзамен в 9 классе —<br>это волнительно")
        hold(page, 2800)
        crossfade_card(
            page,
            "Но к нему можно<br>подготовиться заранее",
            "По порядку, без гонки в последний момент",
        )
        hold(page, 2800)

        go_scene(page, f"{BASE}/about", ".about-hero h1")
        scroll_bottom_clear(page, ".about-metrics", 1100)
        hold(page, 2600, "35 занятий. Понятная программа.<br>Подготовка в своём темпе")
        overlay(page, "")
        slow_scroll_el(page, "#method", 1400, "start")
        hold(page, 2400, "Теория, тест и практика —<br>шаг за шагом")

        if cookies:
            context.add_cookies(cookies)
        go_scene(page, f"{BASE}/cabinet", ".cabinet-title")
        scroll_bottom_clear(page, ".cabinet-stats", 800)
        scroll_top_ratio(page, ".lesson-grid", 0.72, 700)
        hold(page, 2600, "Свой темп.<br>Прогресс виден в кабинете")

        click_scene(
            page,
            page.locator("a.btn-primary", has_text="Продолжить"),
            ".path",
            "",
        )
        slow_scroll_el(page, ".path", 800, "start")
        hold(page, 2200, "Каждое занятие —<br>три шага")
        hold(page, 1400, "Сначала теория")

        click_scene(
            page,
            page.locator("a.path-card", has_text="Теория"),
            ".theory-hero h1",
            "Теорию можно прочитать<br>или посмотреть",
        )
        hold(page, 1800)
        choose_text = page.locator("#chooseText")
        if choose_text.count():
            click_scene(
                page,
                choose_text,
                "#theoryTextCard:not([hidden]) .theory-article",
                "Текст — небольшими шагами",
                fade=False,
            )
        hold(page, 700)
        overlay(page, "")
        show_tutor(page)
        hold(page, 500)
        select_theory_fragment(page, "при счёте предметов")
        hold(page, 1600, "Выделите фрагмент —<br>помощник его объяснит")
        overlay(page, "")
        move_click(page, page.locator("#tutorAskSel"))
        page.wait_for_selector("#tutorLayer:not([hidden])")
        wait_tutor_answer(page)
        pin_tutor_answer(page)
        hold(page, 3600, "ИИ-помощник разбирает<br>выделенный текст")
        overlay(page, "")
        type_slow(page.locator("#tutorInput"), "Что такое рациональное число?", 55)
        hold(page, 400)
        move_click(page, page.locator("#tutorSend"))
        wait_tutor_answer(page)
        pin_tutor_answer(page)
        hold(page, 3600, "И можно просто спросить<br>в чате")
        close_tutor(page)
        hold(page, 300)
        mark_theory_done(page)
        hold(page, 300)

        go_scene(page, f"{BASE}/lessons/1/test", "form.quiz")
        slow_scroll_el(page, "form.quiz", 700, "start")
        hold(page, 1500, "Потом короткий тест")
        overlay(page, "")
        answers = load_test_answers()
        for letter in answers.get("T1", []):
            click_option(page, "T1", letter)
            hold(page, 280)
        slow_scroll_el(page, ".q-card:nth-of-type(2)", 1200, "center")
        hold(page, 250)
        for letter in answers.get("T2", []):
            click_option(page, "T2", letter)
            hold(page, 220)
        fill_remaining_answers(page)
        hold(page, 300)
        slow_scroll_el(page, "form.quiz button[type=submit]", 1400, "center")
        click_scene(
            page,
            page.locator("form.quiz button[type=submit]"),
            ".hero-card h1",
            "Тест по теме пройден",
        )
        hold(page, 2200)

        practice_btn = page.locator("a.btn-primary", has_text="практической")
        if practice_btn.count() == 0:
            practice_btn = page.locator("a.btn-primary", has_text="практик")
        if practice_btn.count():
            click_scene(page, practice_btn.first, ".paper", "")
        else:
            go_scene(page, f"{BASE}/lessons/1/practice", ".paper")
        slow_scroll_el(page, ".paper", 800, "start")
        hold(page, 2400, "Сначала задание решают<br>в тетради")
        upload = page.locator(".upload-form, .drop")
        if upload.count():
            overlay(page, "")
            slow_scroll_el(page, ".upload-form, .drop", 1200, "start")
            hold(page, 900, "Потом прикрепляют<br>фото решения")
            file_input = page.locator("#photoInput")
            if file_input.count():
                overlay(page, "")
                file_input.set_input_files(str(photo))
                try:
                    page.wait_for_selector("#photoPreviewWrap:not([hidden]) #photoPreview", timeout=4000)
                except Exception:
                    file_input.set_input_files(str(photo))
                    page.evaluate(
                        """() => document.getElementById('photoInput')
                          ?.dispatchEvent(new Event('change', { bubbles: true }))"""
                    )
                    page.wait_for_selector("#photoPreviewWrap:not([hidden]) #photoPreview", timeout=8000)
                scroll_bottom_clear(page, ".privacy-check")
                hold(page, 2400, "Фото решения уходит<br>ИИ-агенту")
            privacy = page.locator("#noPersonalData")
            if privacy.count():
                privacy.check()
                hold(page, 600)
        else:
            hold(page, 1600, "Потом прикрепляют<br>фото решения")

        show_pending_message(page, app, photo)
        task_id = seed_check(app, photo)
        checked = f"{BASE}/lessons/1/practice"
        if task_id:
            checked += f"?task={task_id}"
        go_scene(page, checked, ".ok-card")
        if page.locator(".attempt-feed img").count():
            scroll_bottom_clear(page, ".attempt-feed img", 1200)
        hold(page, 2800, "ИИ-агент проверил фото<br>и написал, что получилось")
        if page.locator(".ai-feedback").count():
            overlay(page, "")
            page.evaluate(
                """() => {
                  const el = document.querySelector(".ai-feedback");
                  if (!el || el.previousElementSibling?.classList.contains("reel-agent-label")) return;
                  const label = document.createElement("p");
                  label.className = "reel-agent-label";
                  label.textContent = "Комментарий агента по итогам проверки";
                  label.style.cssText = "margin:14px 0 8px;font:800 17px/1.3 Manrope,sans-serif;color:#0d6e6a;";
                  el.before(label);
                }"""
            )
            slow_scroll_el(page, ".reel-agent-label", 1000, "start")
            hold(page, 2600, "Комментарий агента<br>по итогам проверки")

        go_scene(page, f"{BASE}/cabinet", ".cabinet-stats")
        scroll_bottom_clear(page, ".cabinet-stats", 700)
        scroll_top_ratio(page, ".lesson-grid", 0.72, 700)
        hold(page, 2800, "Весь путь остаётся<br>в личном кабинете")

        fade_out(page)
        page.set_content(END_HTML, wait_until="domcontentloaded")
        fade_in(page)
        hold(page, 3200)

        video = page.video
        context.close()
        browser.close()
        return Path(video.path())


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    photo = WEB_ROOT / "uploads" / "practice" / "2" / "1" / "20260825_095410_1_orig.jpg"

    freeze_practice_checks()
    app = create_app()
    reset_reel_user(app)

    def run() -> None:
        app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False, threaded=True)

    threading.Thread(target=run, daemon=True).start()
    wait_http(f"{BASE}/login")
    cookies = login_cookies()

    webm = record_reel(photo, cookies, app)
    mp4 = OUT_DIR / "instagram_demo.mp4"
    encode_mp4(webm, mp4)
    print(f"READY {mp4} size={mp4.stat().st_size}")


if __name__ == "__main__":
    main()
