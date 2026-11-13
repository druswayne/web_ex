(() => {
  const root = document.querySelector(".theory-progress");
  if (!root) return;
  const header = document.querySelector(".topbar");
  const pinProgress = () => {
    const height = header ? header.offsetHeight : 58;
    const headerTop = header ? parseFloat(getComputedStyle(header).top) || 0 : 0;
    root.style.top = `${headerTop + height}px`;
  };
  pinProgress();
  if (header && "ResizeObserver" in window) {
    new ResizeObserver(pinProgress).observe(header);
  } else {
    window.addEventListener("resize", pinProgress);
  }
  const bar = document.getElementById("theoryBar");
  const barWrap = document.getElementById("theoryBarWrap");
  const pctLabel = document.getElementById("theoryPct");
  const cta = document.getElementById("theoryCta");
  const ctaTitle = document.getElementById("theoryCtaTitle");
  const article = document.getElementById("theoryArticle") || document.querySelector(".theory-article");
  const url = root.dataset.progressUrl;
  const videoUrl = root.dataset.videoUrl || "";
  const lessonNo = root.dataset.lesson || "";
  const modeKey = `theory-mode-${lessonNo}`;
  const WATCH_PASS = 0.9;
  let lastSent = Number(root.dataset.initial || 0);
  let done = root.dataset.done === "1";
  let lastBeat = Date.now();
  let lastActivity = Date.now();
  let pendingSeconds = 0;
  const IDLE_MS = 90000;

  const textCard = document.getElementById("theoryTextCard");
  const chooseVideo = document.getElementById("chooseVideo");
  const chooseText = document.getElementById("chooseText");
  const chooseVideoNote = document.getElementById("chooseVideoNote");
  const chooseTextNote = document.getElementById("chooseTextNote");

  const articleReady = () =>
    Boolean(article) && textCard && !textCard.hidden && article.offsetHeight > 60;

  const measure = () => {
    if (!articleReady()) return lastSent;
    const top = article.getBoundingClientRect().top + window.scrollY;
    const height = article.offsetHeight - window.innerHeight * 0.72;
    if (height <= 0) return 100;
    const y = window.scrollY - top + 40;
    return Math.max(0, Math.min(100, Math.round((y / height) * 100)));
  };

  const markActivity = () => {
    lastActivity = Date.now();
  };

  const takeSeconds = (opts = {}) => {
    const now = Date.now();
    const delta = Math.floor((now - lastBeat) / 1000);
    lastBeat = now;
    const visible = opts.ignoreHidden || document.visibilityState === "visible";
    if (!visible) return 0;
    if (now - lastActivity > IDLE_MS) return 0;
    if (delta <= 0 || delta > 45) return 0;
    return delta;
  };

  const showTheoryDone = (title) => {
    done = true;
    root.dataset.done = "1";
    if (ctaTitle && title) ctaTitle.textContent = title;
    if (cta) cta.hidden = false;
  };

  const send = async (pct, completed, seconds) => {
    if (!url || !articleReady()) return;
    const body = { pct, completed, seconds: seconds || 0 };
    try {
      await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": window.csrfToken || "",
        },
        body: JSON.stringify(body),
        keepalive: true,
      });
    } catch (_) {
      /* сеть не должна ломать чтение */
    }
  };

  const flushTime = (opts = {}) => {
    if (!articleReady()) {
      lastBeat = Date.now();
      return;
    }
    pendingSeconds += takeSeconds(opts);
    if (pendingSeconds <= 0 && opts.forcePct == null) return;
    const pct = opts.forcePct == null ? measure() : opts.forcePct;
    const seconds = pendingSeconds;
    pendingSeconds = 0;
    send(pct, done || pct >= 98, seconds);
  };

  const tick = () => {
    if (!articleReady()) return;
    markActivity();
    const pct = measure();
    if (bar) bar.style.width = `${pct}%`;
    if (pctLabel) pctLabel.textContent = `${pct}%`;
    const completed = pct >= 98;
    if (completed && !done) {
      done = true;
      if (chooseTextNote) chooseTextNote.textContent = "Прочитано до конца";
      showTheoryDone("Теория прочитана");
      pendingSeconds += takeSeconds();
      send(100, true, pendingSeconds);
      pendingSeconds = 0;
      lastSent = 100;
      return;
    }
    if (completed && cta) cta.hidden = false;
    if (pct - lastSent >= 8) {
      lastSent = pct;
      pendingSeconds += takeSeconds();
      send(pct, false, pendingSeconds);
      pendingSeconds = 0;
    }
  };

  const setModePressed = (mode) => {
    chooseVideo?.classList.toggle("is-on", mode === "video");
    chooseText?.classList.toggle("is-on", mode === "text");
    chooseVideo?.setAttribute("aria-pressed", mode === "video" ? "true" : "false");
    chooseText?.setAttribute("aria-pressed", mode === "text" ? "true" : "false");
  };

  const showMode = (mode) => {
    const videoPane = document.getElementById("theoryVideoPane");
    const video = document.getElementById("theoryVideo");
    if (videoPane) videoPane.hidden = mode !== "video";
    if (textCard) textCard.hidden = mode !== "text";
    if (barWrap) barWrap.hidden = mode !== "text";
    if (pctLabel) pctLabel.hidden = mode !== "text";
    const tutorFab = document.getElementById("tutorFab");
    if (tutorFab) tutorFab.hidden = mode !== "text";
    if (mode !== "video") video?.pause();
    setModePressed(mode);
    try {
      sessionStorage.setItem(modeKey, mode);
    } catch (_) {
      /* private mode */
    }
    if (mode === "text") {
      if (window.foldTheorySolutions) window.foldTheorySolutions(article);
      if (window.renderMathInElement && window.katexAutoOpts) {
        window.renderMathInElement(article, window.katexAutoOpts);
      }
      requestAnimationFrame(tick);
    }
  };

  const bindVideo = () => {
    const video = document.getElementById("theoryVideo");
    const player = document.getElementById("theoryPlayer");
    const watchBar = document.getElementById("theoryWatchBar");
    const hint = document.getElementById("theoryWatchHint");
    const pill = document.getElementById("theoryVideoPill");
    if (!video) return;
    let maxTime = 0;
    let videoDone = root.dataset.videoDone === "1";
    let sent = videoDone;
    let snapping = false;

    const duration = () => {
      const d = Number(video.duration);
      return Number.isFinite(d) && d > 0 ? d : 0;
    };

    const watchedPct = () => {
      if (videoDone) return 100;
      const d = duration();
      if (!d) return 0;
      return Math.min(100, Math.round((maxTime / d) * 100));
    };

    const updateWatch = () => {
      const pct = watchedPct();
      if (watchBar) watchBar.style.width = `${pct}%`;
      if (!hint || videoDone) return;
      hint.textContent = `Просмотрено ${pct}%. Для зачёта нужно 90% без перемотки вперёд.`;
    };

    const enough = (watched, d) => d >= 1 && watched + 0.15 >= d * WATCH_PASS;

    const markVideoUi = () => {
      if (pill) {
        pill.classList.add("ok");
        pill.textContent = "засчитано";
      }
      if (chooseVideoNote) chooseVideoNote.textContent = "Просмотрено, теория засчитана";
      if (hint) hint.textContent = "Видео засчитано. Можно перейти к тесту или открыть текст.";
      updateWatch();
    };

    let retryAt = 0;

    const sendVideo = () => {
      if (!videoUrl || sent || Date.now() < retryAt) return;
      const d = duration();
      const watched = maxTime;
      if (!enough(watched, d)) return;
      sent = true;
      fetch(videoUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": window.csrfToken || "",
        },
        body: JSON.stringify({ watched, duration: d }),
        keepalive: true,
      })
        .then((res) => res.json().then((data) => ({ ok: res.ok, data })))
        .then(({ ok, data }) => {
          if (!ok || !data.completed) {
            sent = false;
            retryAt = Date.now() + 4000;
            return;
          }
          showTheoryDone("Видео засчитано");
        })
        .catch(() => {
          sent = false;
          retryAt = Date.now() + 4000;
        });
    };

    const markDone = () => {
      if (videoDone) {
        if (!sent) sendVideo();
        return;
      }
      videoDone = true;
      root.dataset.videoDone = "1";
      markVideoUi();
      sendVideo();
    };

    const noteForwardSeek = () => {
      if (videoDone || snapping) return;
      if ((video.currentTime || 0) > maxTime + 0.2) {
        snapping = true;
        video.currentTime = maxTime;
        requestAnimationFrame(() => {
          snapping = false;
        });
      }
    };

    video.addEventListener("loadedmetadata", () => {
      player?.classList.toggle("is-vertical", video.videoHeight > video.videoWidth);
      updateWatch();
    });
    video.addEventListener("timeupdate", () => {
      const t = video.currentTime || 0;
      const playing = !video.paused && !video.seeking && !snapping && video.playbackRate <= 1;
      if (playing && t <= maxTime + 1.25) maxTime = Math.max(maxTime, t);
      if (t > maxTime + 0.2) noteForwardSeek();
      if (enough(maxTime, duration())) markDone();
      updateWatch();
    });
    video.addEventListener("seeking", noteForwardSeek);
    video.addEventListener("seeked", noteForwardSeek);
    video.addEventListener("ended", () => {
      const d = duration();
      if (d && maxTime + 1.25 >= d) maxTime = d;
      if (enough(maxTime, d)) markDone();
    });
    video.addEventListener("ratechange", () => {
      if (!videoDone && video.playbackRate > 1) video.playbackRate = 1;
    });
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "hidden") sendVideo();
    });
    window.addEventListener("pagehide", sendVideo);
    if (videoDone) markVideoUi();
  };

  bindVideo();

  if (videoUrl) {
    let mode = "";
    try {
      mode = sessionStorage.getItem(modeKey) || "";
    } catch (_) {
      mode = "";
    }
    if (mode !== "video" && mode !== "text") {
      const readDone = lastSent >= 98;
      if (done && root.dataset.videoDone === "1" && !readDone) mode = "video";
      else if (done) mode = "text";
    }
    if (mode === "video" || mode === "text") showMode(mode);
    chooseVideo?.addEventListener("click", () => showMode("video"));
    chooseText?.addEventListener("click", () => showMode("text"));
  }

  window.addEventListener("scroll", tick, { passive: true });
  window.addEventListener("resize", tick);
  document.addEventListener("mousemove", markActivity, { passive: true });
  document.addEventListener("keydown", markActivity);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") {
      flushTime({ ignoreHidden: true });
    } else {
      lastBeat = Date.now();
      lastActivity = Date.now();
    }
  });
  window.addEventListener("pagehide", () => flushTime({ ignoreHidden: true }));
  setInterval(() => flushTime(), 10000);
  document.addEventListener("DOMContentLoaded", () => {
    tick();
    if (articleReady() && measure() >= 98 && !done) {
      done = true;
      if (chooseTextNote) chooseTextNote.textContent = "Прочитано до конца";
      showTheoryDone("Теория прочитана");
      send(100, true, takeSeconds());
    }
  });
})();
