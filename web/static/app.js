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

  function postTaskAction(url, fd) {
    return fetch(url, {
      method: "POST",
      body: fd,
      headers: { Accept: "application/json" },
      credentials: "same-origin",
    }).then(function (r) {
      return r.json();
    });
  }

  function initTaskRows() {
    function taskFd(line) {
      var fd = new FormData();
      fd.append("task_id", line.dataset.taskId);
      fd.append("next", line.dataset.nextUrl || "/today");
      return fd;
    }

    function closeKebab(line) {
      var det = line.querySelector(".task-kebab");
      if (det) det.removeAttribute("open");
    }

    document.addEventListener("click", function (e) {
      if (e.target.closest(".task-kebab")) return;
      document.querySelectorAll(".task-kebab[open]").forEach(function (el) {
        el.removeAttribute("open");
      });
    });

    document.addEventListener("click", function (e) {
      var btn = e.target.closest("[data-action]");
      if (!btn || !btn.closest(".task-kebab-panel")) return;
      var line = btn.closest(".task-line");
      if (!line) return;
      e.preventDefault();
      var action = btn.getAttribute("data-action");
      var fd = taskFd(line);

      if (action === "edit-text") {
        closeKebab(line);
        startEdit(line);
        return;
      }
      if (action === "today") {
        fd.append("preset", "today");
        postTaskAction("/tasks/reschedule_id", fd).then(function (data) {
          if (data.ok) window.location.reload();
          else window.alert(data.message || "Ошибка");
        });
        return;
      }
      if (action === "tomorrow") {
        fd.append("preset", "tomorrow");
        postTaskAction("/tasks/reschedule_id", fd).then(function (data) {
          if (data.ok) window.location.reload();
          else window.alert(data.message || "Ошибка");
        });
        return;
      }
      if (action === "plus2") {
        fd.append("preset", "plus2");
        postTaskAction("/tasks/reschedule_id", fd).then(function (data) {
          if (data.ok) window.location.reload();
          else window.alert(data.message || "Ошибка");
        });
        return;
      }
      if (action === "clear-due") {
        fd.append("preset", "nodate");
        postTaskAction("/tasks/reschedule_id", fd).then(function (data) {
          if (data.ok) window.location.reload();
          else window.alert(data.message || "Ошибка");
        });
        return;
      }
      if (action === "apply-date") {
        var inp = line.querySelector(".task-date-input");
        var d = inp && inp.value;
        if (!d) {
          window.alert("Выбери дату");
          return;
        }
        fd.append("due_date", d);
        postTaskAction("/tasks/reschedule_id", fd).then(function (data) {
          if (data.ok) window.location.reload();
          else window.alert(data.message || "Ошибка");
        });
        return;
      }
      if (action === "time-bucket") {
        var bk = (btn.getAttribute("data-bucket") || "none").trim();
        fd.append("mode", "today_bucket");
        fd.append("bucket", bk);
        fd.append("section_kind", "");
        fd.append("section_date", "");
        postTaskAction("/tasks/drag_move", fd).then(function (data) {
          if (data.ok) window.location.reload();
          else window.alert(data.message || "Ошибка");
        });
        return;
      }
      if (action === "routine-snooze-today") {
        postTaskAction("/tasks/routine_snooze_today", fd).then(function (data) {
          if (data.ok) window.location.reload();
          else window.alert(data.message || "Ошибка");
        });
        return;
      }
      if (action === "delete") {
        if (!window.confirm("Удалить эту задачу?")) return;
        postTaskAction("/tasks/delete_id", fd).then(function (data) {
          if (data.ok) window.location.reload();
          else window.alert(data.message || "Ошибка");
        });
        return;
      }
      if (action === "make-routine") {
        if (
          !window.confirm(
            "Сделать рутиной? Повтор будет по сегодняшнему дню недели — потом можно сменить в меню ⋯."
          )
        )
          return;
        closeKebab(line);
        fd.append("make_routine", "1");
        postTaskAction("/tasks/set_routine_kind", fd).then(function (data) {
          if (data.ok) window.location.reload();
          else window.alert(data.message || "Ошибка");
        });
        return;
      }
      if (action === "make-normal") {
        if (!window.confirm("Сделать обычной задачей на сегодня?")) return;
        closeKebab(line);
        fd.append("make_routine", "0");
        postTaskAction("/tasks/set_routine_kind", fd).then(function (data) {
          if (data.ok) window.location.reload();
          else window.alert(data.message || "Ошибка");
        });
        return;
      }
      if (action === "save-repeat-days") {
        var box = btn.closest(".routine-repeat-box");
        if (!box) return;
        var intervalInp = box.querySelector(".routine-interval-input");
        var rawIv = intervalInp && intervalInp.value.trim();
        if (rawIv) {
          var n = parseInt(rawIv, 10);
          if (n >= 2 && n <= 365) {
            fd.append("repeat_day", "N_DAYS:" + n);
            postTaskAction("/tasks/set_repeat_day", fd).then(function (data) {
              if (data.ok) window.location.reload();
              else window.alert(data.message || "Ошибка");
            });
            return;
          }
          window.alert("Интервал: число от 2 до 365 или оставь пустым.");
          return;
        }
        var daily = box.querySelector(".routine-daily-cb");
        var weekdays = box.querySelectorAll(".routine-weekday-cb:checked");
        if (daily && daily.checked) {
          fd.append("repeat_day", "ежедневно");
        } else {
          if (!weekdays.length) {
            window.alert("Отметь «Ежедневно», интервал в днях или хотя бы один день недели.");
            return;
          }
          fd.append(
            "repeat_day",
            Array.from(weekdays)
              .map(function (c) {
                return c.value;
              })
              .join(",")
          );
        }
        postTaskAction("/tasks/set_repeat_day", fd).then(function (data) {
          if (data.ok) window.location.reload();
          else window.alert(data.message || "Ошибка");
        });
        return;
      }
    });

    document.addEventListener(
      "click",
      function (e) {
        var x = e.target.closest(".task-kebab-close");
        if (!x) return;
        var det = x.closest(".task-kebab");
        if (!det) return;
        e.preventDefault();
        e.stopPropagation();
        e.stopImmediatePropagation();
        det.removeAttribute("open");
      },
      true
    );

    document.addEventListener("change", function (e) {
      var dailyT = e.target.closest(".routine-daily-cb");
      if (dailyT && dailyT.checked) {
        var bx = dailyT.closest(".routine-repeat-box");
        if (bx) {
          bx.querySelectorAll(".routine-weekday-cb").forEach(function (c) {
            c.checked = false;
          });
        }
      }
      var wdT = e.target.closest(".routine-weekday-cb");
      if (wdT && wdT.checked) {
        var bx2 = wdT.closest(".routine-repeat-box");
        if (bx2) {
          var dcb = bx2.querySelector(".routine-daily-cb");
          if (dcb) dcb.checked = false;
        }
      }

      var cat = e.target.closest(".task-category-select");
      if (cat) {
        var line = cat.closest(".task-line");
        if (!line || !cat.value) return;
        var fd = taskFd(line);
        fd.append("category_name", cat.value);
        postTaskAction("/tasks/set_category", fd).then(function (data) {
          if (data.ok) window.location.reload();
          else window.alert(data.message || "Ошибка");
        });
        return;
      }
    });

    function startEdit(line) {
      var display = line.querySelector(".task-text-display");
      var input = line.querySelector(".task-text-input");
      var saveBtn = line.querySelector(".task-text-save");
      if (!display || !input) return;
      input.value = display.textContent.trim();
      display.hidden = true;
      input.hidden = false;
      if (saveBtn) saveBtn.hidden = false;
      input.focus();
      input.select();
    }

    function cancelEdit(line) {
      var display = line.querySelector(".task-text-display");
      var input = line.querySelector(".task-text-input");
      var saveBtn = line.querySelector(".task-text-save");
      if (!display || !input) return;
      input.hidden = true;
      display.hidden = false;
      if (saveBtn) saveBtn.hidden = true;
    }

    function saveEdit(line) {
      var display = line.querySelector(".task-text-display");
      var input = line.querySelector(".task-text-input");
      if (!display || !input) return;
      var fd = taskFd(line);
      fd.append("text", input.value);
      postTaskAction("/tasks/update_text", fd).then(function (data) {
        if (data.ok) {
          display.textContent = input.value.trim();
          cancelEdit(line);
        } else {
          window.alert(data.message || "Не сохранилось");
        }
      });
    }

    document.querySelectorAll(".task-line").forEach(function (line) {
      var display = line.querySelector(".task-text-display");
      var input = line.querySelector(".task-text-input");
      var saveBtn = line.querySelector(".task-text-save");
      if (!display || !input) return;

      if (saveBtn) {
        saveBtn.addEventListener("mousedown", function (e) {
          e.preventDefault();
        });
        saveBtn.addEventListener("click", function (e) {
          e.preventDefault();
          e.stopPropagation();
          if (input.hidden) return;
          var before = display.textContent.trim();
          var after = input.value.trim();
          if (!after) {
            window.alert("Текст не может быть пустым");
            input.focus();
            return;
          }
          if (after === before) {
            cancelEdit(line);
            return;
          }
          saveEdit(line);
        });
      }

      display.addEventListener("dblclick", function (e) {
        e.preventDefault();
        startEdit(line);
      });

      input.addEventListener("keydown", function (e) {
        if (e.key === "Escape") {
          e.preventDefault();
          input.value = display.textContent;
          cancelEdit(line);
        }
        if (e.key === "Enter" && !e.shiftKey) {
          e.preventDefault();
          if (saveBtn && !saveBtn.hidden) saveBtn.click();
        }
      });

      input.addEventListener("blur", function () {
        if (input.hidden) return;
        setTimeout(function () {
          if (input.hidden) return;
          if (line.contains(document.activeElement)) return;
          var before = display.textContent.trim();
          var after = input.value.trim();
          if (after === before) {
            cancelEdit(line);
            return;
          }
          if (!after) {
            window.alert("Текст не может быть пустым");
            input.focus();
            return;
          }
          saveEdit(line);
        }, 180);
      });
    });
  }

  function initTaskDragDrop() {
    var dragTaskId = null;
    var dragLine = null;

    function clearAllDropHover() {
      document.querySelectorAll(".task-drop-section.task-drop-hover").forEach(function (z) {
        z.classList.remove("task-drop-hover");
      });
    }

    document.addEventListener(
      "dragstart",
      function (e) {
        var line = e.target.closest(".task-line.drag-enabled");
        if (!line || !line.dataset.taskId) return;
        if (
          e.target.closest(
            "input, button, textarea, select, summary, label, .task-kebab, .task-cb"
          )
        ) {
          e.preventDefault();
          return;
        }
        e.dataTransfer.effectAllowed = "move";
        e.dataTransfer.setData("text/plain", line.dataset.taskId);
        dragTaskId = line.dataset.taskId;
        dragLine = line;
        line.classList.add("task-line--dragging");
      },
      false
    );

    document.addEventListener(
      "dragend",
      function () {
        clearAllDropHover();
        if (dragLine) dragLine.classList.remove("task-line--dragging");
        dragTaskId = null;
        dragLine = null;
      },
      false
    );

    document.addEventListener(
      "dragover",
      function (e) {
        if (!dragTaskId) return;
        var zone = e.target.closest(".task-drop-section");
        clearAllDropHover();
        if (zone) {
          e.preventDefault();
          e.dataTransfer.dropEffect = "move";
          zone.classList.add("task-drop-hover");
        }
      },
      false
    );

    document.addEventListener(
      "drop",
      function (e) {
        if (!dragTaskId || !dragLine) return;
        var zone = e.target.closest(".task-drop-section");
        if (!zone) return;
        e.preventDefault();
        clearAllDropHover();
        var nextUrl = dragLine.dataset.nextUrl || "/tasks";
        var fd = new FormData();
        fd.append("task_id", dragTaskId);
        fd.append("next", nextUrl);
        var dk = zone.getAttribute("data-drop-kind");
        if (dk === "today_bucket") {
          var b = zone.getAttribute("data-drop-bucket");
          if (!b) return;
          fd.append("mode", "today_bucket");
          fd.append("bucket", b);
          fd.append("section_kind", "");
          fd.append("section_date", "");
        } else if (dk === "tasks_section") {
          var sk = zone.getAttribute("data-section-kind");
          if (!sk) return;
          if (sk === "nodate" && dragLine.dataset.taskRoutine === "1") {
            window.alert(
              "Рутину нельзя перенести в «Без срока» — перетащи на дату в календаре."
            );
            return;
          }
          fd.append("mode", "tasks_section");
          fd.append("bucket", "");
          fd.append("section_kind", sk);
          fd.append("section_date", zone.getAttribute("data-section-date") || "");
        } else {
          return;
        }
        postTaskAction("/tasks/drag_move", fd).then(function (data) {
          if (data.ok) window.location.reload();
          else window.alert(data.message || "Ошибка");
        });
      },
      false
    );
  }

  document.addEventListener("DOMContentLoaded", function () {
    initNav();
    initVoice();
    initTaskRows();
    initTaskDragDrop();
  });
})();
