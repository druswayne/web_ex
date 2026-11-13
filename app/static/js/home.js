(() => {
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  document.body.classList.add("js");

  const list = $("#routeList");
  const rows = $$(".prow");
  const total = list ? Number(list.dataset.total) || rows.length : rows.length;
  const moreBtn = $("#progMore");
  const fade = $("#progFade");
  const shown = $("#progShown");
  let filter = "all";
  let expanded = false;

  const applyProg = (anim) => {
    const vis = [];
    rows.forEach((row) => {
      const ok = (filter === "all" || row.dataset.stage === filter) && (filter !== "all" || expanded || Number(row.dataset.n) <= 8);
      row.classList.toggle("hide", !ok);
      if (ok) vis.push(row);
    });
    vis.forEach((row, i) => {
      row.style.setProperty("--i", String(Math.min(i, 14)));
      if (anim && !reduced) {
        row.style.animation = "none";
        void row.offsetWidth;
        row.style.animation = "";
      }
    });
    const collapsed = filter === "all" && !expanded;
    fade?.classList.toggle("off", !collapsed);
    if (moreBtn) {
      moreBtn.hidden = filter !== "all";
      moreBtn.setAttribute("aria-expanded", expanded ? "true" : "false");
      moreBtn.innerHTML = expanded
        ? 'Свернуть программу <svg style="transform:rotate(180deg)"><use href="#i-chev"/></svg>'
        : `Показать все ${total} занятий <svg><use href="#i-chev"/></svg>`;
    }
    if (shown) shown.textContent = `Показано ${vis.length} из ${total}`;
  };
  moreBtn?.addEventListener("click", () => {
    expanded = !expanded;
    applyProg(true);
  });
  $$(".fchip").forEach((chip) => {
    chip.addEventListener("click", () => {
      $$(".fchip").forEach((item) => item.classList.remove("active"));
      chip.classList.add("active");
      filter = chip.dataset.f || "all";
      applyProg(true);
    });
  });
  if (rows.length) applyProg(false);

  const panel = $("#lvPanel");
  $$(".lvbtn").forEach((button) => {
    button.addEventListener("click", () => {
      const index = Number(button.dataset.i);
      $$(".lvbtn").forEach((item) => {
        item.classList.remove("active");
        item.setAttribute("aria-pressed", "false");
      });
      button.classList.add("active");
      button.setAttribute("aria-pressed", "true");
      if (!panel) return;
      panel.dataset.l = String(index);
      const title = $("#lvTitle");
      const desc = $("#lvDesc");
      const tag = $("#lvTag");
      if (title) title.textContent = button.dataset.title || "";
      if (desc) desc.textContent = button.dataset.desc || "";
      if (tag) tag.textContent = button.dataset.tag || "";
      $$("#lvMeter i").forEach((mark, i) => mark.classList.toggle("on", i <= index));
      panel.classList.remove("swap");
      void panel.offsetWidth;
      panel.classList.add("swap");
    });
  });

  const plural = (n, one, few, many) => {
    const mod = n % 10;
    const hun = n % 100;
    if (mod === 1 && hun !== 11) return one;
    if (mod >= 2 && mod <= 4 && (hun < 12 || hun > 14)) return few;
    return many;
  };
  const pace = $("#pace");
  const dots = $("#paceDots");
  const updPace = () => {
    if (!pace) return;
    const n = Number(pace.value);
    const weeks = Math.ceil(total / n);
    const paceN = $("#paceN");
    const paceWeeks = $("#paceWeeks");
    const paceLbl = $("#paceWeeksLbl");
    if (paceN) paceN.textContent = `${n} ${plural(n, "занятие", "занятия", "занятий")} в неделю`;
    if (paceWeeks) paceWeeks.textContent = `≈ ${weeks}`;
    if (paceLbl) paceLbl.textContent = `${plural(weeks, "неделя", "недели", "недель")} на весь курс`;
    if (dots) {
      dots.innerHTML = "";
      for (let i = 0; i < weeks; i += 1) {
        const mark = document.createElement("i");
        mark.style.animationDelay = `${i * 22}ms`;
        dots.appendChild(mark);
      }
    }
  };
  pace?.addEventListener("input", updPace);
  updPace();

  const runCnt = (el) => {
    const target = Number(el.dataset.count);
    if (reduced) {
      el.textContent = String(target);
      return;
    }
    let t0 = null;
    const tick = (t) => {
      if (!t0) t0 = t;
      let p = Math.min((t - t0) / 1300, 1);
      p = 1 - (1 - p) ** 3;
      el.textContent = String(Math.round(target * p));
      if (p < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  };

  const cab = $("#cab");
  const cabRun = () => {
    const fill = $("#cabFill");
    const num = $("#cabNum");
    const done = cab ? Number(cab.dataset.done) || 0 : 0;
    const width = total ? `${(done / total) * 100}%` : "0";
    if (reduced) {
      cab?.classList.add("s1", "s2", "s3");
      if (fill) fill.style.width = width;
      if (num) num.textContent = String(done);
      return;
    }
    setTimeout(() => cab?.classList.add("s1"), 700);
    setTimeout(() => cab?.classList.add("s2"), 1400);
    setTimeout(() => cab?.classList.add("s3"), 2100);
    setTimeout(() => {
      if (fill) fill.style.width = width;
      if (!num) return;
      let t0 = null;
      const tk = (t) => {
        if (!t0) t0 = t;
        const p = Math.min((t - t0) / 1200, 1);
        num.textContent = String(Math.round(done * (1 - (1 - p) ** 3)));
        if (p < 1) requestAnimationFrame(tk);
      };
      requestAnimationFrame(tk);
    }, 500);
  };

  const mock = $("#mock");
  const status = $("#mockStatus");
  let scanTimer = null;
  const scanRun = () => {
    if (!mock || !status) return;
    clearTimeout(scanTimer);
    mock.classList.remove("scanning", "stamped");
    status.classList.remove("ok");
    status.textContent = "проверяю…";
    if (reduced) {
      mock.classList.add("stamped");
      status.classList.add("ok");
      status.textContent = "зачтено ✓";
      return;
    }
    void mock.offsetWidth;
    mock.classList.add("scanning");
    scanTimer = setTimeout(() => {
      mock.classList.add("stamped");
      status.classList.add("ok");
      status.textContent = "зачтено ✓";
    }, 1650);
  };
  $("#scanBtn")?.addEventListener("click", scanRun);

  $$("[data-stagger]").forEach((group) => {
    Array.from(group.children).forEach((child, i) => child.style.setProperty("--i", String(i)));
  });
  const fired = {};
  const fire = (id) => {
    if (fired[id]) return;
    fired[id] = true;
    if (id === "top") {
      cabRun();
      $$(".cnt").forEach(runCnt);
    }
    if (id === "program") applyProg(true);
    if (id === "check") scanRun();
  };
  if ("IntersectionObserver" in window) {
    const io = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        entry.target.classList.add("in");
        const section = entry.target.closest("section");
        if (section && section.id) fire(section.id);
        io.unobserve(entry.target);
      });
    }, { threshold: 0.12, rootMargin: "0px 0px -30px 0px" });
    $$(".rv, .stepper").forEach((el) => io.observe(el));
    const hero = $(".hero");
    hero?.classList.add("in");
    fire("top");
  } else {
    $$(".rv, .stepper").forEach((el) => el.classList.add("in"));
    $(".hero")?.classList.add("in");
    cabRun();
    $$(".cnt").forEach(runCnt);
    mock?.classList.add("stamped");
    if (status) {
      status.classList.add("ok");
      status.textContent = "зачтено ✓";
    }
  }

  const bar = $("#scrollBar");
  const nav = $(".topbar");
  const fab = $("#toTop");
  const chalks = $$(".chalk-f");
  let ticking = false;
  const onScroll = () => {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(() => {
      const y = window.scrollY || window.pageYOffset;
      const h = document.documentElement.scrollHeight - window.innerHeight;
      if (bar) bar.style.width = `${h > 0 ? (y / h) * 100 : 0}%`;
      nav?.classList.toggle("scrolled", y > 10);
      fab?.classList.toggle("show", y > 700);
      if (!reduced) {
        chalks.forEach((chalk) => {
          const speed = Number(chalk.dataset.speed || "0");
          chalk.style.marginTop = `${-y * speed}px`;
        });
      }
      ticking = false;
    });
  };
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();
  fab?.addEventListener("click", () => {
    window.scrollTo({ top: 0, behavior: reduced ? "auto" : "smooth" });
  });
})();
