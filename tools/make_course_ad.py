"""Ролик по сценарию: экзамен близко, затем крупный телефон с живым сайтом."""
from __future__ import annotations

import shutil
import sys
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

from make_instagram_reel import (
    BASE,
    OUT_DIR,
    PORT,
    WEB_ROOT,
    freeze_practice_checks,
    load_test_answers,
    login_cookies,
    reset_reel_user,
    seed_check,
    wait_http,
)
from make_style_ad import prepare_progress
from app import create_app

AD_DIR = OUT_DIR / "course_ad"
SHOTS = AD_DIR / "shots"
PHOTO = WEB_ROOT / "uploads" / "practice" / "2" / "1" / "20260825_095410_1_orig.jpg"
TOTAL_MS = 166000
CLIPS = AD_DIR / "clips"

HIDE_JS = """() => {
  const style = document.createElement("style");
  style.textContent = `
    footer.site-foot, .who, .cabinet-balance, aside.tips, .to-top,
    .about-jump, .cabinet-title a, .cookie-banner, .flash,
    .topnav a[href*="messages"] { display: none !important; }
  `;
  document.head.appendChild(style);
}"""


def shoot(page, url: str, dest: Path, ready: str, after=None) -> None:
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_selector(ready, timeout=20000)
    page.evaluate(HIDE_JS)
    if after:
        after(page)
    page.wait_for_timeout(500)
    dest.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(dest))
    print("shot", dest.name, flush=True)


def capture(cookies: list[dict], app, photo: Path) -> None:
    task_id = seed_check(app, photo)
    if not task_id:
        raise RuntimeError("Не удалось подготовить практику")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 390, "height": 844},
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
                  const top = el.getBoundingClientRect().top + window.scrollY - 12;
                  window.scrollTo(0, Math.max(0, top));
                }"""
            )

        def theory(pg):
            pg.locator("#chooseText").click()
            pg.wait_for_selector("#theoryTextCard:not([hidden]) .theory-article")
            pg.wait_for_timeout(400)

        def practice(pg):
            pg.wait_for_selector(".ai-feedback")
            pg.evaluate(
                """() => {
                  const el = document.querySelector('.ai-feedback');
                  if (!el || el.previousElementSibling?.classList.contains('reel-agent-label')) return;
                  const label = document.createElement('p');
                  label.className = 'reel-agent-label';
                  label.textContent = 'Комментарий ИИ ассистента по итогам проверки';
                  label.style.cssText = 'margin:14px 0 8px;font:800 16px/1.3 Manrope,sans-serif;color:#0d6e6a;';
                  el.before(label);
                  document.querySelector('.attempt-feed img, .ai-feedback')?.scrollIntoView({block:'start'});
                }"""
            )

        shoot(page, f"{BASE}/about", SHOTS / "about.png", "h1")
        shoot(page, f"{BASE}/cabinet", SHOTS / "cabinet.png", ".cabinet-title")
        shoot(page, f"{BASE}/cabinet", SHOTS / "tiles.png", ".lesson-grid", tiles)
        shoot(page, f"{BASE}/lessons/1/theory", SHOTS / "theory.png", ".theory-hero h1", theory)
        shoot(page, f"{BASE}/lessons/1/test", SHOTS / "test.png", ".q-card")
        shoot(
            page,
            f"{BASE}/lessons/1/practice?task={task_id}",
            SHOTS / "check.png",
            ".ai-feedback",
            practice,
        )
        browser.close()


def ad_html(social: bool = False) -> str:
    html = r"""<!DOCTYPE html>
<html lang="ru" class="__KIND__">
<head>
<meta charset="utf-8">
<style>
  * { box-sizing: border-box; }
  html, body { margin: 0; width: 1280px; height: 720px; overflow: hidden; background: #14181c; }
  body { font-family: "Segoe UI", sans-serif; color: #243044; }
  .bg, .layer { position: absolute; inset: 0; }
  video.bg {
    width: 100%; height: 100%; object-fit: cover; opacity: 0;
    filter: saturate(.78) contrast(1.04);
    transition: opacity .45s linear;
  }
  video.bg.on { opacity: 1; }
  video.bg.cold { filter: saturate(.62) contrast(1.05) hue-rotate(8deg); }
  .shade {
    position: absolute; inset: 0; pointer-events: none;
    background: linear-gradient(180deg, rgba(0,0,0,.08), rgba(0,0,0,.28) 70%, rgba(0,0,0,.45));
  }
  .pill {
    position: absolute; left: 50%; bottom: 48px; transform: translateX(-50%);
    width: min(86%, 980px); margin: 0; text-align: center; z-index: 3;
    background: rgba(255, 250, 244, .94); color: #2e3850;
    font-size: 30px; font-weight: 600; line-height: 1.3; letter-spacing: -.02em;
    padding: 16px 28px; border-radius: 18px;
    box-shadow: 0 12px 30px rgba(20,16,12,.2);
  }
  .site {
    position: absolute; inset: 0; display: none; background: #efeae1;
  }
  .site.on { display: block; }
  .site h2 {
    position: absolute; left: 64px; top: 50%; transform: translateY(-50%);
    width: 600px; margin: 0; font-weight: 560; font-size: 40px;
    line-height: 1.2; letter-spacing: -.03em; color: #2a3142;
  }
  .phone {
    position: absolute; top: 24px; right: 168px;
    width: 324px; height: 672px; border-radius: 42px; background: #111;
    border: 12px solid #1a1a1a; overflow: hidden;
    box-shadow: 0 28px 50px rgba(40, 32, 24, .18);
  }
  .phone img, .phone video {
    width: 100%; height: 100%; object-fit: cover; object-position: top center;
    display: block; background: #f6f1e8;
  }
  .mark {
    position: absolute; right: 46px; bottom: 72px;
    width: 96px; height: 96px; border-radius: 28px;
    background: conic-gradient(from 220deg, #0d6e6a, #7bc4b8, #e0a15a, #0d6e6a);
    box-shadow: inset 0 0 0 5px #fff;
  }
  .end {
    position: absolute; inset: 0; display: none; background: #efeae1;
    padding: 64px 90px; 
  }
  .end.on { display: flex; flex-direction: column; justify-content: center; }
  .end .lead { font-size: 46px; font-weight: 650; letter-spacing: -.04em; line-height: 1.15; margin: 0 0 28px; }
  .end h2 { margin: 0; font-size: 64px; letter-spacing: -.04em; }
  .end p { margin: 10px 0 0; font-size: 32px; color: #3c465c; }
  .end .url { margin-top: 22px; color: #0d6e6a; font-size: 40px; font-weight: 800; }
  html.social, html.social body { width: 720px; height: 1280px; }
  html.social .pill { bottom: 190px; font-size: 28px; width: min(92%, 660px); }
  html.social .site h2 {
    left: 36px; right: 36px; top: 44px; width: auto; transform: none; font-size: 32px;
  }
  html.social .phone {
    left: 151px; right: auto; top: 248px;
    width: 418px; height: 872px; border-width: 14px; border-radius: 48px;
  }
  html.social .mark { right: 36px; bottom: 56px; width: 72px; height: 72px; border-radius: 22px; }
  html.social .end { padding: 72px 40px; }
  html.social .end .lead { font-size: 34px; }
  html.social .end h2 { font-size: 46px; }
  html.social .end p { font-size: 24px; }
  html.social .end .url { font-size: 30px; }
</style>
</head>
<body>
  <video class="bg" data-clip="desk" src="stock/50111.mp4" muted loop playsinline></video>
  <video class="bg" data-clip="worry" src="stock/4531.mp4" muted loop playsinline></video>
  <video class="bg" data-clip="calm" src="stock/48095.mp4" muted loop playsinline></video>
  <video class="bg" data-clip="notes" src="../style_ad/stock/45148-720.mp4" muted loop playsinline></video>
  <div class="shade" id="shade"></div>
  <p class="pill" id="pill"></p>
  <section class="site" id="site">
    <h2 id="siteText"></h2>
    <div class="phone">
      <img id="phone" alt="">
      <video id="phoneVid" muted playsinline hidden></video>
    </div>
    <div class="mark"></div>
  </section>
  <section class="end" id="end">
    <p class="lead" id="endLead">Не готовься к экзамену<br>в последний момент.</p>
    <h2>Математика 9</h2>
    <p>35 последовательных занятий</p>
    <p>Подготовка в удобном темпе</p>
    <p class="url">ex-helper.tw1.su</p>
  </section>
<script>
const steps = [
  {t:0,    kind:"film", clip:"desk",  cold:true,  text:"Выпускной экзамен по математике уже близко"},
  {t:6.6,  kind:"film", clip:"worry", cold:true,  text:"Для многих это волнение"},
  {t:12.8, kind:"film", clip:"worry", cold:true,  text:"Ещё успею повторить"},
  {t:16.6, kind:"film", clip:"worry", cold:true,  text:"Разберусь перед экзаменом"},
  {t:19.6, kind:"film", clip:"worry", cold:true,  text:"Как-нибудь спишу"},
  {t:21.8, kind:"film", clip:"desk",  cold:true,  text:"За несколько дней всю программу не повторить"},
  {t:28.8, kind:"film", clip:"worry", cold:true,  text:"Списывание — не подготовка"},
  {t:38.6, kind:"film", clip:"worry", cold:false, text:"Начни готовиться заранее"},
  {t:42.2, kind:"film", clip:"worry", cold:false, text:"Без спешки, тема за темой"},
  {t:49, kind:"site", img:"shots/about.png", text:"• 35 занятий по порядку<br>К занятию тема уже пройдена в школе<br>Одно занятие — на неделю"},
  {t:62, kind:"clip", src:"clips/theory.webm", text:"• В материале — детали, ловушки<br>и примеры решений<br>ИИ ассистент объяснит выделенный фрагмент"},
  {t:74, kind:"clip", src:"clips/video.webm", text:"• Материалы урока<br>представлены текстом и видео"},
  {t:82, kind:"clip", src:"clips/test.webm", text:"• После изучения материала<br>предлагается тест по теме"},
  {t:90, kind:"clip", src:"clips/path.webm", text:"• После теста открывается<br>блок практических заданий"},
  {t:100, kind:"clip", src:"clips/level.webm", text:"• Уровень сложности выбирается<br>в настройках личного кабинета"},
  {t:108, kind:"clip", src:"clips/tasks.webm", text:"• Практические задания<br>выбранного уровня"},
  {t:116, kind:"clip", src:"clips/check.webm", text:"• Решение в тетради прикрепляют<br>к задаче, ИИ ассистент его проверяет"},
  {t:128, kind:"clip", src:"clips/pending.webm", text:"• Пока одно задание проверяется,<br>можно решать другие"},
  {t:140, kind:"card", lead:"Не готовься к экзамену<br>в последний момент.<br>Начни готовиться заранее."}
];
function begin() {
  document.querySelectorAll("video.bg").forEach(v => { v.muted = true; v.play(); });
  const t0 = performance.now();
  const pill = document.getElementById("pill");
  const shade = document.getElementById("shade");
  const site = document.getElementById("site");
  const end = document.getElementById("end");
  const phone = document.getElementById("phone");
  const siteText = document.getElementById("siteText");
  const endLead = document.getElementById("endLead");
  let shown = "";
  function frame() {
    const t = (performance.now() - t0) / 1000;
    let cur = steps[0];
    for (const s of steps) if (t >= s.t) cur = s;
    const film = cur.kind === "film";
    const onSite = cur.kind === "site" || cur.kind === "clip";
    document.querySelectorAll("video.bg").forEach(v => {
      v.classList.toggle("on", film && v.dataset.clip === cur.clip);
      v.classList.toggle("cold", !!(film && cur.cold));
    });
    shade.style.display = film ? "block" : "none";
    pill.style.display = film ? "block" : "none";
    if (film) pill.textContent = cur.text;
    site.classList.toggle("on", onSite);
    if (onSite && shown !== (cur.src || cur.img)) {
      shown = cur.src || cur.img;
      siteText.innerHTML = cur.text;
      phone.hidden = cur.kind !== "site";
      if (cur.kind === "site") phone.src = cur.img;
    }
    end.classList.toggle("on", cur.kind === "card");
    if (cur.kind === "card") endLead.innerHTML = cur.lead;
    if (t < 166) requestAnimationFrame(frame);
  }
  frame();
}
function arm() {
  const clips = [...document.querySelectorAll("video.bg")];
  if (clips.every((x) => x.readyState >= 2) && !window.__began) {
    window.__began = true;
    begin();
  }
}
document.querySelectorAll("video.bg").forEach((v) => v.addEventListener("canplay", arm));
arm();
</script>
</body>
</html>
"""
    return html.replace("__KIND__", "social" if social else "wide")


def hide(page) -> None:
    page.evaluate(HIDE_JS)


def glide(page, steps: int, delta: int, pause: int) -> None:
    page.mouse.move(200, 420)
    for _ in range(steps):
        page.mouse.wheel(0, delta)
        page.wait_for_timeout(pause)


def clip_theory(page) -> None:
    question = "Поясни выделенное: почему 0 не считают натуральным числом?"
    page.goto(f"{BASE}/lessons/1/theory", wait_until="domcontentloaded")
    page.wait_for_selector("#chooseText")
    hide(page)
    page.evaluate(
        """() => {
          const style = document.createElement('style');
          style.textContent = '::selection{background:#b7ebc6;color:#14221c}';
          document.head.appendChild(style);
          const orig = window.fetch.bind(window);
          const answer = '<p>В школьном курсе натуральные числа начинаются с 1. Ноль — целое и рациональное, но не натуральное. На экзамене это частая ловушка.</p>';
          window.fetch = async (url, opts) => {
            const method = String((opts && opts.method) || 'GET').toUpperCase();
            const target = String(url);
            if (target.includes('/theory/chat') && method === 'GET') {
              return new Response(JSON.stringify({ok: true, messages: [], balance: ''}), {
                status: 200, headers: {'Content-Type': 'application/json'}
              });
            }
            if (target.includes('/theory/chat') && method === 'POST') {
              await new Promise((resolve) => setTimeout(resolve, 1400));
              return new Response(JSON.stringify({
                ok: true, pending: false,
                assistant: {id: 1, html: answer}
              }), {status: 200, headers: {'Content-Type': 'application/json'}});
            }
            return orig(url, opts);
          };
        }"""
    )
    page.wait_for_timeout(300)
    page.click("#chooseText")
    page.wait_for_selector("#theoryTextCard:not([hidden]) .theory-article")
    page.locator("#theoryTextCard").locator("text=/ловушк/i").first.scroll_into_view_if_needed()
    page.wait_for_timeout(900)
    page.evaluate(
        """() => {
          const article = document.querySelector('#theoryTextCard .theory-article');
          const node = [...article.querySelectorAll('li')].find((el) =>
            /не выбирают/i.test(el.textContent)
          );
          node.scrollIntoView({block: 'center'});
          node.style.background = '#b7ebc6';
          node.style.borderRadius = '8px';
          node.style.boxShadow = 'inset 0 0 0 2px #1f6b45';
          const range = document.createRange();
          range.selectNodeContents(node);
          const sel = getSelection();
          sel.removeAllRanges();
          sel.addRange(range);
          document.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
        }"""
    )
    page.wait_for_selector("#tutorAskSel:not([hidden])")
    page.wait_for_timeout(3200)
    page.evaluate("() => document.getElementById('tutorFab').click()")
    page.wait_for_selector("#tutorInput")
    page.wait_for_timeout(350)
    page.locator("#tutorInput").click()
    page.locator("#tutorInput").press_sequentially(question, delay=42)
    page.wait_for_timeout(350)
    page.locator("#tutorSend").click()
    page.wait_for_selector(".tutor-msg-assistant:not(.is-typing)")
    page.wait_for_timeout(3200)


def clip_video(page) -> None:
    page.goto(f"{BASE}/lessons/1/theory", wait_until="domcontentloaded")
    page.wait_for_selector("#chooseVideo")
    hide(page)
    page.wait_for_timeout(2200)
    page.click("#chooseVideo")
    page.wait_for_selector("#theoryVideo")
    page.locator("#theoryPlayer").scroll_into_view_if_needed()
    page.evaluate(
        """() => {
          const v = document.getElementById('theoryVideo');
          v.muted = true;
          return v.play();
        }"""
    )
    page.wait_for_timeout(8000)


def clip_test(page) -> None:
    page.goto(f"{BASE}/lessons/1/test", wait_until="domcontentloaded")
    page.wait_for_selector(".q-card")
    hide(page)
    page.wait_for_timeout(500)
    for code, letters in list(load_test_answers().items())[:4]:
        head = page.locator(f"#qhead-{code}")
        head.scroll_into_view_if_needed()
        page.wait_for_timeout(350)
        for letter in letters:
            page.locator(
                f'fieldset[aria-labelledby="qhead-{code}"] input[value="{letter}"]'
            ).click()
        page.wait_for_timeout(2200)


def clip_path(page) -> None:
    page.goto(f"{BASE}/lessons/1", wait_until="domcontentloaded")
    page.wait_for_selector(".path")
    hide(page)
    page.locator(".path").scroll_into_view_if_needed()
    page.wait_for_timeout(7000)


def clip_level(page) -> None:
    page.goto(f"{BASE}/cabinet/profile", wait_until="domcontentloaded")
    page.wait_for_selector("select[name=level_band]")
    hide(page)
    page.locator("select[name=level_band]").scroll_into_view_if_needed()
    page.evaluate(
        """() => {
          const sel = document.querySelector('select[name=level_band]');
          const menu = document.createElement('ul');
          menu.style.cssText = 'margin:8px 0 0;padding:6px;list-style:none;background:#fff;border-radius:14px;box-shadow:0 12px 28px rgba(20,16,12,.14);';
          for (const opt of sel.options) {
            const li = document.createElement('li');
            li.textContent = opt.textContent.trim();
            li.style.cssText = 'padding:11px 12px;border-radius:10px;font:650 16px/1.3 Segoe UI,sans-serif;color:#243044;';
            if (opt.selected) {
              li.style.background = '#e5f3f1';
              li.style.color = '#0d6e6a';
            }
            menu.append(li);
          }
          sel.insertAdjacentElement('afterend', menu);
        }"""
    )
    page.wait_for_timeout(7000)


def clip_tasks(page) -> None:
    page.goto(f"{BASE}/lessons/1/practice", wait_until="domcontentloaded")
    page.wait_for_selector(".task-dots a")
    hide(page)
    dots = page.locator(".task-dots a")
    count = dots.count()
    indexes = list(range(2, min(count, 5))) or list(range(min(count, 3)))
    for index in indexes:
        page.locator(".task-dots a").nth(index).click()
        page.wait_for_selector(".paper")
        page.wait_for_timeout(3200)


def clip_check(page, task_id: int) -> None:
    page.goto(f"{BASE}/lessons/1/practice?task={task_id}", wait_until="domcontentloaded")
    page.wait_for_selector(".ai-feedback")
    hide(page)
    page.evaluate(
        """() => {
          const el = document.querySelector('.ai-feedback');
          if (!el || el.previousElementSibling?.classList.contains('reel-agent-label')) return;
          const label = document.createElement('p');
          label.className = 'reel-agent-label';
          label.textContent = 'Комментарий ИИ ассистента по итогам проверки';
          label.style.cssText = 'margin:8px 0;font:800 16px/1.3 Segoe UI,sans-serif;color:#0d6e6a;';
          el.before(label);
          el.closest('li')?.scrollIntoView({block: 'start'});
        }"""
    )
    page.wait_for_timeout(8000)


def clip_pending(page, task_id: int) -> None:
    page.goto(f"{BASE}/lessons/1/practice?task={task_id}", wait_until="domcontentloaded")
    page.wait_for_selector("#pendingCard")
    hide(page)
    page.locator("#pendingCard").scroll_into_view_if_needed()
    page.wait_for_timeout(8000)


def save_clip(page, name: str) -> None:
    video = page.video
    page.context.close()
    CLIPS.mkdir(parents=True, exist_ok=True)
    dest = CLIPS / f"{name}.webm"
    shutil.move(video.path(), dest)
    print("clip", dest.name, flush=True)


def seed_scenes(app, photo: Path) -> tuple[int, int]:
    """Готовит чат и проверку без вызова платного агента."""
    from app.config import UPLOADS_DIR
    from app.models import PracticeAssignment, PracticeAttempt, TheoryChatMessage, User, db

    with app.app_context():
        user = User.find_by_username("reel")
        TheoryChatMessage.query.filter_by(user_id=user.id, lesson_number=1).delete()
        rows = (
            PracticeAssignment.query.filter_by(user_id=user.id, lesson_number=1, is_active=True)
            .order_by(PracticeAssignment.slot.asc())
            .all()
        )
        if len(rows) < 2:
            raise RuntimeError("Для ролика нужны хотя бы два практических задания")
        for attempt in rows[0].attempts:
            if attempt.ai_verdict == "correct":
                attempt.ai_feedback = (
                    "Выражение больше. ИИ ассистент сверил шаги по фото решения."
                )
        second = rows[1]
        folder = UPLOADS_DIR / "practice" / str(user.id) / str(second.id)
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / "solution.jpg"
        shutil.copyfile(photo, dest)
        rel = f"practice/{user.id}/{second.id}/solution.jpg"
        db.session.add(
            PracticeAttempt(
                assignment_id=second.id,
                user_id=user.id,
                attempt_no=1,
                photo_filename=rel,
                processed_filename=rel,
                is_correct=False,
                ai_verdict="pending",
                ai_feedback="Проверяем решение…",
            )
        )
        db.session.commit()
        checked = rows[0].id
        return checked, second.id


def record_flows(cookies: list[dict], checked_id: int, pending_id: int) -> None:
    actions = (
        ("theory", clip_theory),
        ("video", clip_video),
        ("test", clip_test),
        ("path", clip_path),
        ("level", clip_level),
        ("tasks", clip_tasks),
        ("check", lambda page: clip_check(page, checked_id)),
        ("pending", lambda page: clip_pending(page, pending_id)),
    )
    (AD_DIR / "clips_raw").mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--autoplay-policy=no-user-gesture-required"],
        )
        for name, action in actions:
            context = browser.new_context(
                viewport={"width": 390, "height": 844},
                record_video_dir=str(AD_DIR / "clips_raw"),
                record_video_size={"width": 390, "height": 844},
            )
            context.add_cookies(
                [*cookies, {"name": "cookie_consent", "value": "accepted", "url": BASE + "/"}]
            )
            page = context.new_page()
            action(page)
            save_clip(page, name)
        browser.close()


def record(social: bool = False) -> Path:
    html_path = AD_DIR / ("social.html" if social else "index.html")
    html_path.write_text(ad_html(social), encoding="utf-8")
    raw = AD_DIR / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    width, height = (720, 1280) if social else (1280, 720)
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--autoplay-policy=no-user-gesture-required", "--allow-file-access-from-files"],
        )
        context = browser.new_context(
            viewport={"width": width, "height": height},
            record_video_dir=str(raw),
            record_video_size={"width": width, "height": height},
        )
        page = context.new_page()
        page.goto(html_path.as_uri())
        page.wait_for_function("() => window.__began === true", timeout=20000)
        page.wait_for_timeout(TOTAL_MS)
        video = page.video
        context.close()
        browser.close()
        return Path(video.path())


def composite(
    base: Path | None = None,
    dest: Path | None = None,
    screen: tuple[int, int, int, int] = (1206, 56, 436, 966),
    tag: str = "t",
) -> Path:
    """Вкладывает записи сайта в экран телефона.

    Снимающий браузер не записывает кадры вложенного video, поэтому
    клипы накладываются на уже собранный ролик.
    """
    import subprocess

    import imageio_ffmpeg
    from PIL import Image, ImageDraw

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    base = base or (AD_DIR / "base.mp4")
    dest = dest or (OUT_DIR / "math9_course_ad.mp4")
    if not base.exists():
        raise SystemExit(f"Нет ролика: {base}")
    mask_path = CLIPS / "mask.png"
    mask = Image.new("L", (screen[2], screen[3]), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, screen[2] - 1, screen[3] - 1), radius=46, fill=255
    )
    mask.save(mask_path)

    slots = (
        ("theory.webm", 4.0, 62, 12),
        ("video.webm", 0.3, 74, 8),
        ("test.webm", 0.3, 82, 8),
        ("path.webm", 0.2, 90, 10),
        ("level.webm", 0.2, 100, 8),
        ("tasks.webm", 0.2, 108, 8),
        ("check.webm", 0.2, 116, 12),
        ("pending.webm", 0.2, 128, 12),
    )
    masks = "".join(f"[m{i}]" for i in range(len(slots)))
    parts = [f"[{len(slots) + 1}:v]format=gray,fps=30,split={len(slots)}{masks}"]
    labels = []
    for index, (_name, _skip, start, length) in enumerate(slots):
        src = f"{index + 1}:v"
        raw = f"r{index}"
        out = f"c{index}"
        labels.append((out, start, start + length))
        parts.append(
            f"[{src}]scale={screen[2]}:{screen[3]}:flags=lanczos:force_original_aspect_ratio=increase,"
            f"crop={screen[2]}:{screen[3]},"
            f"fps=30,format=yuva420p[{raw}];"
            f"[{raw}][m{index}]alphamerge,"
            f"tpad=stop_mode=clone:stop_duration={length},"
            f"trim=duration={length},setpts=PTS-STARTPTS+{start}/TB[{out}]"
        )
    current = "0:v"
    chain = []
    for index, (label, start, end) in enumerate(labels):
        nxt = f"v{index}"
        chain.append(
            f"[{current}][{label}]overlay={screen[0]}:{screen[1]}:eof_action=pass:"
            f"enable='gte(t,{start})*lt(t,{end})'[{nxt}]"
        )
        current = nxt
    graph = ";".join(parts + chain)
    staged = dest.with_name(dest.stem + "_phone.mp4")
    subprocess.check_call(
        [
            ffmpeg, "-y", "-i", str(base),
            *[
                arg
                for name, skip, _start, _length in slots
                for arg in ("-ss", str(skip), "-i", str(CLIPS / name))
            ],
            "-framerate", "30", "-loop", "1", "-t", "30", "-i", str(mask_path),
            "-filter_complex", graph,
            "-map", f"[{current}]",
            "-c:v", "libx264", "-crf", "18", "-preset", "medium",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an",
            str(staged),
        ]
    )
    shutil.move(staged, dest)
    frames = AD_DIR / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    for sec in (33, 48, 56, 66, 80, 90, 110, 124, 136, 148):
        subprocess.check_call(
            [
                ffmpeg, "-y", "-ss", str(sec), "-i", str(dest),
                "-frames:v", "1", "-update", "1", str(frames / f"{tag}{sec:03d}.png"),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    print(f"READY {dest} size={dest.stat().st_size}")
    return dest


def encode(webm: Path, base: Path, scale: str) -> Path:
    import subprocess

    import imageio_ffmpeg

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    subprocess.check_call(
        [
            ffmpeg, "-y", "-i", str(webm),
            "-vf", f"scale={scale}:flags=lanczos,fps=30,format=yuv420p",
            "-c:v", "libx264", "-crf", "18", "-preset", "medium",
            "-movflags", "+faststart", "-an", str(base),
        ]
    )
    return base


def main() -> None:
    if not PHOTO.exists():
        raise SystemExit(f"Нет фото решения: {PHOTO}")
    AD_DIR.mkdir(parents=True, exist_ok=True)
    freeze_practice_checks()
    app = create_app()
    reset_reel_user(app)
    prepare_progress(app)

    def run() -> None:
        app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False, threaded=True)

    threading.Thread(target=run, daemon=True).start()
    wait_http(f"{BASE}/login")
    cookies = login_cookies()
    if not (SHOTS / "about.png").exists():
        capture(cookies, app, PHOTO)
    else:
        seed_check(app, PHOTO)
    checked_id, pending_id = seed_scenes(app, PHOTO)
    record_flows(cookies, checked_id, pending_id)
    wide = record(False)
    social = record(True)
    encode(wide, AD_DIR / "base.mp4", "1920:1080")
    composite(
        AD_DIR / "base.mp4",
        OUT_DIR / "math9_course_ad.mp4",
        (1206, 56, 436, 966),
        "t",
    )
    encode(social, AD_DIR / "base_social.mp4", "1080:1920")
    composite(
        AD_DIR / "base_social.mp4",
        OUT_DIR / "math9_course_ad_social.mp4",
        (248, 394, 584, 1266),
        "s",
    )


def finish_theory() -> None:
    """Переснимает только теорию и заново собирает оба ролика."""
    freeze_practice_checks()
    app = create_app()

    def run() -> None:
        app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False, threaded=True)

    threading.Thread(target=run, daemon=True).start()
    wait_http(f"{BASE}/login")
    cookies = login_cookies()
    (AD_DIR / "clips_raw").mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--autoplay-policy=no-user-gesture-required"],
        )
        context = browser.new_context(
            viewport={"width": 390, "height": 844},
            record_video_dir=str(AD_DIR / "clips_raw"),
            record_video_size={"width": 390, "height": 844},
        )
        context.add_cookies(
            [*cookies, {"name": "cookie_consent", "value": "accepted", "url": BASE + "/"}]
        )
        page = context.new_page()
        clip_theory(page)
        save_clip(page, "theory")
        browser.close()
    composite(AD_DIR / "base.mp4", OUT_DIR / "math9_course_ad.mp4", (1206, 56, 436, 966), "t")
    composite(
        AD_DIR / "base_social.mp4",
        OUT_DIR / "math9_course_ad_social.mp4",
        (248, 394, 584, 1266),
        "s",
    )


def mux_eleven(picture: Path) -> Path:
    """Кладёт уже записанный голос на новые таймкоды картинки."""
    import subprocess

    import imageio_ffmpeg

    voice = OUT_DIR / "math9_course_ad_social_eleven.mp4"
    dest = OUT_DIR / "math9_course_ad_social_eleven.mp4"
    staged = OUT_DIR / "math9_course_ad_social_eleven_new.mp4"
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    # Куски исходной дорожки: вступление как есть, прогресс на карту занятия,
    # финал на конечную карточку. Фраза про учителя не используется.
    graph = (
        "anullsrc=r=44100:cl=mono:d=166[base];"
        "[1:a]atrim=0:73.2,asetpts=PTS-STARTPTS[a0];"
        "[1:a]atrim=77.8:86.9,asetpts=PTS-STARTPTS,adelay=90000:all=1[a1];"
        "[1:a]atrim=74.2:77.9,asetpts=PTS-STARTPTS,adelay=140000:all=1[a2];"
        "[1:a]atrim=94.3:113.3,asetpts=PTS-STARTPTS,adelay=144200:all=1[a3];"
        "[base][a0][a1][a2][a3]amix=inputs=5:normalize=0:duration=first[a]"
    )
    subprocess.check_call(
        [
            ffmpeg, "-y",
            "-i", str(picture),
            "-i", str(voice),
            "-filter_complex", graph,
            "-map", "0:v", "-map", "[a]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
            "-movflags", "+faststart",
            str(staged),
        ]
    )
    shutil.move(staged, dest)
    print(f"READY {dest} size={dest.stat().st_size}")
    return dest


def rebuild_shells() -> None:
    wide = record(False)
    social = record(True)
    encode(wide, AD_DIR / "base.mp4", "1920:1080")
    composite(AD_DIR / "base.mp4", OUT_DIR / "math9_course_ad.mp4", (1206, 56, 436, 966), "t")
    encode(social, AD_DIR / "base_social.mp4", "1080:1920")
    picture = composite(
        AD_DIR / "base_social.mp4",
        OUT_DIR / "math9_course_ad_social.mp4",
        (248, 394, 584, 1266),
        "s",
    )
    mux_eleven(picture)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    if len(sys.argv) > 1 and sys.argv[1] == "composite":
        composite()
    elif len(sys.argv) > 1 and sys.argv[1] == "theory":
        finish_theory()
    elif len(sys.argv) > 1 and sys.argv[1] == "shells":
        rebuild_shells()
    elif len(sys.argv) > 1 and sys.argv[1] == "layout":
        encode(record(False), AD_DIR / "base.mp4", "1920:1080")
    else:
        main()
