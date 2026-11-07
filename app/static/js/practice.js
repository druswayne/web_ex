(() => {
  const input = document.getElementById("photoInput");
  const preview = document.getElementById("photoPreview");
  const wrap = document.getElementById("photoPreviewWrap");
  const form = document.getElementById("practiceForm");
  const btn = document.getElementById("sendBtn");
  const hint = document.getElementById("waitHint");
  const clearBtn = document.getElementById("photoClear");
  const privacy = document.getElementById("noPersonalData");
  let objectUrl = "";
  let sending = false;
  let generation = 0;
  let prepareTask = null;
  const prepared = new WeakSet();
  const MAX_SIDE = 1600;
  const JPEG_QUALITY = 0.93;

  const toCheckJpeg = async (file) => {
    if (!file || prepared.has(file)) return file;
    if (!/^image\//.test(file.type) || typeof createImageBitmap !== "function") return file;
    let bitmap;
    try {
      bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
    } catch (_err) {
      return file;
    }
    try {
      const long = Math.max(bitmap.width, bitmap.height);
      const scale = Math.min(1, MAX_SIDE / long);
      const canvas = document.createElement("canvas");
      canvas.width = Math.max(1, Math.round(bitmap.width * scale));
      canvas.height = Math.max(1, Math.round(bitmap.height * scale));
      const ctx = canvas.getContext("2d", { alpha: false });
      if (!ctx) return file;
      ctx.fillStyle = "#fff";
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
      const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", JPEG_QUALITY));
      if (!blob) return file;
      const base = (file.name || "solution").replace(/\.[^.]+$/i, "") || "solution";
      const photo = new File([blob], `${base}.jpg`, { type: "image/jpeg", lastModified: Date.now() });
      prepared.add(photo);
      return photo;
    } finally {
      bitmap.close();
    }
  };

  const usePrepared = (file) => {
    if (!input || !file || file === input.files[0]) return;
    const data = new DataTransfer();
    data.items.add(file);
    input.files = data.files;
    showPreview(file);
  };

  const showPreview = (file) => {
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    objectUrl = URL.createObjectURL(file);
    preview.src = objectUrl;
    preview.hidden = false;
    if (wrap) wrap.hidden = false;
  };

  const syncSend = () => {
    if (!btn || !privacy || sending) return;
    btn.disabled = !privacy.checked;
  };

  const resetPrivacy = () => {
    if (!privacy) return;
    privacy.checked = false;
    syncSend();
  };

  const clearPhoto = () => {
    generation += 1;
    prepareTask = null;
    if (!input) return;
    input.value = "";
    if (objectUrl) {
      URL.revokeObjectURL(objectUrl);
      objectUrl = "";
    }
    preview.removeAttribute("src");
    preview.hidden = true;
    if (wrap) wrap.hidden = true;
    resetPrivacy();
  };

  if (input && preview) {
    input.addEventListener("change", () => {
      const file = input.files && input.files[0];
      if (!file) {
        clearPhoto();
        return;
      }
      const token = ++generation;
      resetPrivacy();
      showPreview(file);
      prepareTask = toCheckJpeg(file).then((small) => {
        if (token !== generation) return small;
        usePrepared(small);
        return small;
      }).catch(() => file);
    });
  }

  if (clearBtn) clearBtn.addEventListener("click", clearPhoto);
  if (privacy) privacy.addEventListener("change", syncSend);
  syncSend();

  if (form && btn) {
    form.addEventListener("submit", async (event) => {
      if (sending) return;
      if (privacy && !privacy.checked) {
        event.preventDefault();
        syncSend();
        privacy.reportValidity();
        return;
      }
      const file = input && input.files && input.files[0];
      if (!file) return;
      event.preventDefault();
      btn.disabled = true;
      btn.textContent = "Готовим фото…";
      if (hint) hint.hidden = false;
      try {
        const small = await (prepareTask || toCheckJpeg(file));
        usePrepared(small);
      } catch (_err) {
        /* уйдёт исходный файл */
      }
      sending = true;
      btn.textContent = "Отправляем…";
      form.submit();
    });
  }

  const script = document.currentScript;
  const statusUrl = script && script.dataset.statusUrl;
  const watchPending = script && script.dataset.pending === "1";
  if (!statusUrl || !watchPending) return;

  let currentWasPending = Boolean(document.getElementById("pendingCard"));
  const tick = async () => {
    try {
      const res = await fetch(statusUrl, { headers: { Accept: "application/json" } });
      if (!res.ok) return;
      const data = await res.json();
      const pending = new Set(data.pending_ids || []);
      const correct = new Set(data.correct_ids || []);
      document.querySelectorAll(".task-dots a[data-id]").forEach((a) => {
        const id = Number(a.dataset.id);
        a.classList.toggle("wait", pending.has(id));
        a.classList.toggle("ok", correct.has(id));
      });
      if (currentWasPending && !data.current_pending) {
        window.location.reload();
      }
      currentWasPending = Boolean(data.current_pending);
    } catch (_err) {
      /* сеть могла моргнуть — следующий тик повторит */
    }
  };
  setInterval(tick, 4000);
})();
