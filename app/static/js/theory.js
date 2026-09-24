(() => {
  const root = document.querySelector(".theory-progress");
  if (!root) return;
  const bar = document.getElementById("theoryBar");
  const barWrap = document.getElementById("theoryBarWrap");
  const pctLabel = document.getElementById("theoryPct");
  const cta = document.getElementById("theoryCta");
  const article = document.getElementById("theoryArticle") || document.querySelector(".theory-article");
  const url = root.dataset.progressUrl;
  const videoUrl = root.dataset.videoUrl || "";
  const lessonNo = root.dataset.lesson || "";
  const openKey = `theory-text-open-${lessonNo}`;
  let lastSent = Number(root.dataset.initial || 0);
  let done = root.dataset.done === "1";
  let lastBeat = Date.now();
  let lastActivity = Date.now();
  let pendingSeconds = 0;
  const IDLE_MS = 90000;

  const articleReady = () =>
    Boolean(article) && !article.hidden && article.offsetHeight > 60;

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
    if (completed && cta) cta.hidden = false;
    if (completed && !done) {
      done = true;
      pendingSeconds += takeSeconds();
      send(100, true, pendingSeconds);
      pendingSeconds = 0;
      lastSent = 100;
      return;
    }
    if (pct - lastSent >= 8) {
      lastSent = pct;
      pendingSeconds += takeSeconds();
      send(pct, false, pendingSeconds);
      pendingSeconds = 0;
    }
  };

  const toggle = document.getElementById("theoryTextToggle");
  const lead = document.getElementById("theoryTextLead");
  const card = document.getElementById("theoryTextCard");
  let textUnlocked = root.dataset.textUnlocked === "1";

  const setExpanded = (open) => {
    if (!article) return;
    article.hidden = !open;
    if (toggle) {
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
      toggle.textContent = open ? "Свернуть" : "Развернуть";
    }
    if (barWrap) barWrap.hidden = !open;
    if (pctLabel) pctLabel.hidden = !open;
    if (open) {
      if (window.foldTheorySolutions) window.foldTheorySolutions(article);
      if (window.renderMathInElement && window.katexAutoOpts) {
        window.renderMathInElement(article, window.katexAutoOpts);
      }
      try {
        sessionStorage.setItem(openKey, "1");
      } catch (_) {
        /* private mode */
      }
      requestAnimationFrame(tick);
    } else if (lessonNo) {
      try {
        sessionStorage.setItem(openKey, "0");
      } catch (_) {
        /* private mode */
      }
    }
  };

  const unlockText = () => {
    textUnlocked = true;
    root.dataset.textUnlocked = "1";
    if (card) card.dataset.locked = "0";
    if (toggle) {
      toggle.removeAttribute("aria-disabled");
      toggle.classList.remove("btn-ghost");
      toggle.classList.add("btn-primary");
      toggle.textContent = article && !article.hidden ? "Свернуть" : "Развернуть";
    }
    if (lead) {
      lead.textContent = "Текст, формулы и разобранные примеры. Можно открывать в любой момент.";
    }
    const tutorFab = document.getElementById("tutorFab");
    if (tutorFab) tutorFab.hidden = false;
    const pill = document.getElementById("theoryVideoPill");
    if (pill) {
      pill.classList.add("ok");
      pill.textContent = "просмотрено";
    }
    const hint = document.getElementById("theoryWatchHint");
    if (hint) {
      hint.textContent = "Видео просмотрено — текстовый разбор можно открывать в любой момент.";
    }
  };

  if (toggle) {
    toggle.addEventListener("click", () => {
      if (!textUnlocked) {
        const pane = document.getElementById("theoryVideoPane");
        const video = document.getElementById("theoryVideo");
        pane?.scrollIntoView({ behavior: "smooth", block: "center" });
        video?.play?.().catch(() => {});
        return;
      }
      setExpanded(Boolean(article?.hidden));
    });
  }

  const bindVideo = () => {
    const video = document.getElementById("theoryVideo");
    const player = document.getElementById("theoryPlayer");
    const watchBar = document.getElementById("theoryWatchBar");
    if (!video) return;
    let maxTime = 0;
    let videoDone = root.dataset.videoDone === "1";
    let sent = videoDone;
    let snapping = false;

    const duration = () => {
      const d = Number(video.duration);
      return Number.isFinite(d) && d > 0 ? d : 0;
    };

    const updateWatch = () => {
      const d = duration();
      const pct = videoDone ? 100 : d ? Math.min(100, Math.round((maxTime / d) * 100)) : 0;
      if (watchBar) watchBar.style.width = `${pct}%`;
    };

    const enough = (watched, d) =>
      d >= 1 && watched + 0.05 >= Math.min(d * 0.92, Math.max(0, d - 0.4));

    const markDone = () => {
      if (videoDone) return;
      videoDone = true;
      root.dataset.videoDone = "1";
      unlockText();
      setExpanded(true);
      updateWatch();
      sendVideo();
    };

    const sendVideo = () => {
      if (!videoUrl || sent) return;
      const d = duration();
      const watched = Math.max(maxTime, video.currentTime || 0);
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
      }).catch(() => {
        sent = false;
      });
    };

    const clampSeek = () => {
      if (videoDone || snapping) return;
      if (video.currentTime > maxTime + 0.35) {
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
      if (!video.seeking && !snapping) {
        maxTime = Math.max(maxTime, video.currentTime || 0);
      }
      const d = duration();
      if (d && maxTime >= d - 0.4) markDone();
      updateWatch();
    });
    video.addEventListener("seeking", clampSeek);
    video.addEventListener("seeked", clampSeek);
    video.addEventListener("ended", () => {
      maxTime = Math.max(maxTime, duration());
      markDone();
    });
    video.addEventListener("ratechange", () => {
      if (video.playbackRate > 1) video.playbackRate = 1;
    });
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "hidden") sendVideo();
    });
    window.addEventListener("pagehide", sendVideo);
    if (videoDone) updateWatch();
  };

  bindVideo();

  if (videoUrl && textUnlocked) {
    let preferOpen = done;
    try {
      preferOpen = done || sessionStorage.getItem(openKey) === "1";
    } catch (_) {
      /* private mode */
    }
    if (preferOpen) setExpanded(true);
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
      send(100, true, takeSeconds());
    }
  });
})();
