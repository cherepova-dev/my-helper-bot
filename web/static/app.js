(function () {
  "use strict";

  function initNav() {
    var shell = document.getElementById("app-shell");
    var openBtn = document.querySelector("[data-open-nav]");
    var closeEls = document.querySelectorAll("[data-close-nav]");
    var sidebar = document.getElementById("app-sidebar");
    if (!shell || !openBtn) return;

    function setOpen(open) {
      if (open) {
        shell.classList.add("nav-open");
        openBtn.setAttribute("aria-expanded", "true");
        document.body.style.overflow = "hidden";
      } else {
        shell.classList.remove("nav-open");
        openBtn.setAttribute("aria-expanded", "false");
        document.body.style.overflow = "";
      }
    }

    openBtn.addEventListener("click", function () {
      setOpen(!shell.classList.contains("nav-open"));
    });

    closeEls.forEach(function (el) {
      el.addEventListener("click", function () {
        setOpen(false);
      });
    });

    if (sidebar) {
      sidebar.querySelectorAll("a[href]").forEach(function (a) {
        a.addEventListener("click", function () {
          if (window.matchMedia("(max-width: 768px)").matches) setOpen(false);
        });
      });
    }

    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") setOpen(false);
    });
  }

  function postVoiceBlob(endpoint, blob, filename) {
    var fd = new FormData();
    fd.append("file", blob, filename || "voice.webm");
    return fetch(endpoint, {
      method: "POST",
      body: fd,
      headers: { Accept: "application/json" },
      credentials: "same-origin",
    }).then(function (r) {
      return r.json();
    });
  }

  function initVoiceRoot(root) {
    var endpoint = root.getAttribute("data-voice-endpoint");
    if (!endpoint) return;

    var btn = root.querySelector("[data-voice-record]");
    var statusEl = root.querySelector("[data-voice-status]");
    var fileInput = root.querySelector("[data-voice-file]");

    function showStatus(text, isErr) {
      if (!statusEl) return;
      statusEl.textContent = text || "";
      statusEl.classList.toggle("voice-status-err", !!isErr);
    }

    function afterResult(data) {
      showStatus(data.message || "", !data.ok);
      if (data.ok) window.location.reload();
    }

    if (fileInput) {
      fileInput.addEventListener("change", function () {
        var f = fileInput.files && fileInput.files[0];
        if (!f) return;
        showStatus("Отправка файла…", false);
        postVoiceBlob(endpoint, f, f.name).then(afterResult).catch(function () {
          showStatus("Ошибка сети.", true);
        });
        fileInput.value = "";
      });
    }

    if (!btn) return;

    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      btn.disabled = true;
      showStatus("Запись с микрофона недоступна — выберите аудиофайл.", false);
      return;
    }

    var mediaRecorder = null;
    var chunks = [];
    var recording = false;

    btn.addEventListener("click", function () {
      if (!recording) {
        chunks = [];
        navigator.mediaDevices
          .getUserMedia({ audio: true })
          .then(function (stream) {
            var mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
              ? "audio/webm;codecs=opus"
              : MediaRecorder.isTypeSupported("audio/webm")
                ? "audio/webm"
                : "";
            mediaRecorder = mime
              ? new MediaRecorder(stream, { mimeType: mime })
              : new MediaRecorder(stream);
            mediaRecorder.ondataavailable = function (e) {
              if (e.data && e.data.size) chunks.push(e.data);
            };
            mediaRecorder.onstop = function () {
              stream.getTracks().forEach(function (t) {
                t.stop();
              });
              var blob = new Blob(chunks, {
                type: mediaRecorder.mimeType || "audio/webm",
              });
              if (!blob.size) {
                showStatus("Пустая запись.", true);
                recording = false;
                btn.textContent = "Записать голосом";
                return;
              }
              showStatus("Распознаём…", false);
              postVoiceBlob(endpoint, blob, "voice.webm")
                .then(afterResult)
                .catch(function () {
                  showStatus("Ошибка сети.", true);
                });
              recording = false;
              btn.textContent = "Записать голосом";
            };
            mediaRecorder.start();
            recording = true;
            btn.textContent = "Закончить и отправить";
            showStatus("Идёт запись… Нажми ещё раз, чтобы отправить.", false);
          })
          .catch(function () {
            showStatus("Нет доступа к микрофону.", true);
          });
      } else {
        if (mediaRecorder && mediaRecorder.state !== "inactive") {
          mediaRecorder.stop();
        }
      }
    });
  }

  function initVoice() {
    document.querySelectorAll("[data-voice-root]").forEach(initVoiceRoot);
  }

  document.addEventListener("DOMContentLoaded", function () {
    initNav();
    initVoice();
  });
})();
