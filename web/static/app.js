/* ============================================================
   Observation Agent — UI

   Consumes the agent's SSE event stream and renders two things at once:
   the prose answer, and the tool timeline that produced it. Showing the
   tool calls is the point — an agent you can audit beats one you have to
   trust.
   ============================================================ */
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };

  var el = {
    transcript: $("transcript"),
    welcome: $("welcome"),
    welcomeExamples: $("welcome-examples"),
    form: $("composer"),
    input: $("question-input"),
    send: $("send-btn"),
    stop: $("stop-btn"),
    groups: $("question-groups"),
    reportList: $("report-list"),
    refreshReports: $("refresh-reports"),
    chips: $("dataset-chips"),
    asOf: $("as-of"),
    backendBadge: $("backend-badge"),
    backendLabel: $("backend-label"),
    backendDialog: $("backend-dialog"),
    backendBody: $("backend-body"),
    backendScrim: $("backend-scrim"),
    backendClose: $("backend-close"),
    drawer: $("drawer"),
    drawerScrim: $("drawer-scrim"),
    drawerTitle: $("drawer-title"),
    drawerBody: $("drawer-body"),
    drawerClose: $("drawer-close"),
    drawerCopy: $("drawer-copy"),
    themeToggle: $("theme-toggle")
  };

  var state = {
    config: null,
    busy: false,
    controller: null,
    history: [],
    reportsSeen: {},
    currentReport: null
  };

  /* ---------- theme ---------- */
  var THEME_KEY = "oa-theme";
  function applyTheme(mode) {
    document.documentElement.setAttribute("data-theme", mode);
    el.themeToggle.textContent = mode === "dark" ? "☾" : mode === "light" ? "☀" : "◐";
    el.themeToggle.title = "Theme: " + mode + " (click to change)";
  }
  applyTheme(localStorage.getItem(THEME_KEY) || "auto");
  el.themeToggle.addEventListener("click", function () {
    var order = ["auto", "light", "dark"];
    var next = order[(order.indexOf(document.documentElement.getAttribute("data-theme")) + 1) % 3];
    localStorage.setItem(THEME_KEY, next);
    applyTheme(next);
  });

  /* ---------- boot ---------- */
  function boot() {
    fetch("/api/config")
      .then(function (r) { return r.json(); })
      .then(function (cfg) {
        state.config = cfg;
        renderChips(cfg.dataset);
        renderQuestions(cfg.questions);
        renderExamples(cfg.questions);
        renderReports(cfg.reports || []);
        renderBackend(cfg);
        if (cfg.dataset && cfg.dataset.today) el.asOf.textContent = cfg.dataset.today;
        var deepLink = new URLSearchParams(location.search).get("report");
        if (deepLink) openReport(deepLink, true);
      })
      .catch(function (err) {
        el.backendLabel.textContent = "config failed";
        el.backendBadge.classList.add("down");
        console.error(err);
      });
  }

  function renderChips(ds) {
    if (!ds || ds.error || !ds.counts) {
      el.chips.innerHTML = '<span class="chip">dataset unavailable</span>';
      return;
    }
    var c = ds.counts;
    var chips = [
      ["as of", ds.today],
      ["orgs", c.organizations],
      ["projects", c.projects],
      ["associates", c.associates],
      ["observations", c.observations.toLocaleString()],
      ["responses", c.responses.toLocaleString()],
      ["notes", c.coach_notes]
    ];
    el.chips.innerHTML = chips.map(function (p) {
      return '<span class="chip"><strong>' + p[1] + "</strong> " + p[0] + "</span>";
    }).join("");
  }

  function renderQuestions(groups) {
    if (!groups) return;
    el.groups.innerHTML = Object.keys(groups).map(function (group) {
      var items = groups[group].map(function (q) {
        return (
          '<button class="qbtn" type="button" data-q="' + md.escapeHtml(q.question) + '">' +
          '<span class="qnum">' + q.number + "</span>" +
          '<span class="qtext">' + md.escapeHtml(q.short) + "</span>" +
          "</button>"
        );
      }).join("");
      return '<div class="qgroup"><div class="qgroup-label">' + group + "</div>" + items + "</div>";
    }).join("");

    el.groups.addEventListener("click", function (ev) {
      var btn = ev.target.closest(".qbtn");
      if (btn && !state.busy) submit(btn.getAttribute("data-q"));
    });
  }

  function renderExamples(groups) {
    if (!groups) return;
    var picks = [];
    Object.keys(groups).forEach(function (g) {
      (groups[g] || []).slice(0, 2).forEach(function (q) { picks.push(q); });
    });
    el.welcomeExamples.innerHTML = picks.slice(0, 6).map(function (q) {
      return '<button class="example-pill" type="button" data-q="' +
        md.escapeHtml(q.question) + '">' + md.escapeHtml(q.short) + "</button>";
    }).join("");
    el.welcomeExamples.addEventListener("click", function (ev) {
      var pill = ev.target.closest(".example-pill");
      if (pill && !state.busy) submit(pill.getAttribute("data-q"));
    });
  }

  function renderBackend(cfg) {
    var live = cfg.runnable && cfg.runnable.length;
    el.backendBadge.classList.toggle("live", !!live);
    el.backendBadge.classList.toggle("down", !live);
    if (live) {
      var info = cfg.backends[cfg.default_backend];
      el.backendLabel.textContent = info.label + " · " + info.model;
    } else {
      el.backendLabel.textContent = "no model backend";
    }

    var rows = Object.keys(cfg.backends).map(function (name) {
      var b = cfg.backends[name];
      return (
        '<div class="backend-row">' +
        '<span class="mark ' + (b.available ? "on" : "off") + '">' + (b.available ? "●" : "○") + "</span>" +
        "<div><div class=\"bl\">" + md.escapeHtml(b.label) +
        (name === cfg.default_backend ? " <em>(active)</em>" : "") + "</div>" +
        '<div class="br">' + md.escapeHtml(b.reason) + " · model <code>" +
        md.escapeHtml(String(b.model)) + "</code></div></div></div>"
      );
    }).join("");

    if (!live) {
      rows += '<div class="setup-hint"><strong>No backend available.</strong> ' +
        "The UI loads but cannot answer. Enable either one:" +
        "<pre>export ANTHROPIC_API_KEY=sk-ant-...\n# or, in a terminal:\nclaude   # then /login</pre></div>";
    }
    el.backendBody.innerHTML = rows;
  }

  function renderReports(reports) {
    if (!reports || !reports.length) {
      el.reportList.innerHTML = '<li class="empty-note">None yet — answers write reports here.</li>';
      return;
    }
    el.reportList.innerHTML = reports.map(function (r) {
      return '<li><button class="report-btn" type="button" data-name="' +
        md.escapeHtml(r.name) + '"><span class="rt">' + md.escapeHtml(r.title) +
        '</span><span class="rp">' + md.escapeHtml(r.path) + "</span></button></li>";
    }).join("");
  }

  el.reportList.addEventListener("click", function (ev) {
    var btn = ev.target.closest(".report-btn");
    if (btn) openReport(btn.getAttribute("data-name"));
  });

  function refreshReports() {
    return fetch("/api/reports")
      .then(function (r) { return r.json(); })
      .then(function (d) { renderReports(d.reports || []); });
  }
  el.refreshReports.addEventListener("click", refreshReports);

  /* ---------- report drawer ---------- */
  function openReport(name, skipUrl) {
    state.currentReport = null;
    if (!skipUrl) {
      var url = new URL(location.href);
      url.searchParams.set("report", name);
      history.replaceState(null, "", url);
    }
    el.drawerTitle.textContent = name;
    el.drawerBody.innerHTML = '<p class="empty-note">Loading…</p>';
    el.drawer.hidden = false;
    el.drawerScrim.hidden = false;
    fetch("/api/reports/" + encodeURIComponent(name))
      .then(function (r) {
        if (!r.ok) throw new Error("not found");
        return r.json();
      })
      .then(function (d) {
        state.currentReport = d.markdown;
        el.drawerBody.innerHTML = md.render(d.markdown);
        var h1 = el.drawerBody.querySelector("h1");
        if (h1) { el.drawerTitle.textContent = h1.textContent; h1.remove(); }
        el.drawerBody.scrollTop = 0;
      })
      .catch(function () {
        el.drawerBody.innerHTML = '<p class="empty-note">Could not load this report.</p>';
      });
  }
  function closeDrawer() {
    el.drawer.hidden = true;
    el.drawerScrim.hidden = true;
    var url = new URL(location.href);
    if (url.searchParams.has("report")) {
      url.searchParams.delete("report");
      history.replaceState(null, "", url);
    }
  }
  el.drawerClose.addEventListener("click", closeDrawer);
  el.drawerScrim.addEventListener("click", closeDrawer);
  el.drawerCopy.addEventListener("click", function () {
    if (!state.currentReport) return;
    navigator.clipboard.writeText(state.currentReport).then(function () {
      el.drawerCopy.textContent = "Copied";
      setTimeout(function () { el.drawerCopy.textContent = "Copy"; }, 1400);
    });
  });

  /* ---------- backend dialog ---------- */
  function closeBackend() { el.backendDialog.hidden = true; el.backendScrim.hidden = true; }
  el.backendBadge.addEventListener("click", function () {
    el.backendDialog.hidden = false; el.backendScrim.hidden = false;
  });
  el.backendClose.addEventListener("click", closeBackend);
  el.backendScrim.addEventListener("click", closeBackend);
  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape") { closeDrawer(); closeBackend(); }
  });

  /* ---------- composer ---------- */
  function autosize() {
    el.input.style.height = "auto";
    el.input.style.height = Math.min(el.input.scrollHeight, 200) + "px";
  }
  el.input.addEventListener("input", autosize);
  el.input.addEventListener("keydown", function (ev) {
    if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); el.form.requestSubmit(); }
  });
  el.form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    var q = el.input.value.trim();
    if (q) submit(q);
  });
  el.stop.addEventListener("click", function () {
    if (state.controller) state.controller.abort();
  });

  function setBusy(busy) {
    state.busy = busy;
    el.send.disabled = busy;
    el.stop.hidden = !busy;
    el.send.querySelector(".send-label").textContent = busy ? "Thinking…" : "Ask";
    el.groups.querySelectorAll(".qbtn").forEach(function (b) { b.disabled = busy; });
  }

  function scrollDown() {
    el.transcript.scrollTop = el.transcript.scrollHeight;
  }

  /* ---------- one turn ---------- */
  function submit(question) {
    if (state.busy) return;
    if (el.welcome) { el.welcome.remove(); el.welcome = null; }
    el.input.value = "";
    autosize();

    var turn = document.createElement("section");
    turn.className = "turn";
    turn.innerHTML =
      '<div class="turn-user"><div class="bubble-user"></div></div>' +
      '<div class="agent-head"><span class="agent-avatar">◆</span><span class="agent-status">working…</span></div>' +
      '<div class="timeline"></div>' +
      '<div class="answer markdown streaming"></div>' +
      '<div class="reports-row"></div>' +
      '<div class="turn-foot" hidden></div>';
    turn.querySelector(".bubble-user").textContent = question;
    el.transcript.appendChild(turn);
    scrollDown();

    var ui = {
      status: turn.querySelector(".agent-status"),
      timeline: turn.querySelector(".timeline"),
      answer: turn.querySelector(".answer"),
      reportsRow: turn.querySelector(".reports-row"),
      foot: turn.querySelector(".turn-foot")
    };

    var buffer = "";
    var cards = {};
    var newReports = false;

    setBusy(true);
    state.controller = new AbortController();

    fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: question, history: state.history }),
      signal: state.controller.signal
    })
      .then(function (res) {
        if (!res.ok || !res.body) throw new Error("HTTP " + res.status);
        var reader = res.body.getReader();
        var decoder = new TextDecoder();
        var pending = "";

        function pump() {
          return reader.read().then(function (chunk) {
            if (chunk.done) return finish();
            pending += decoder.decode(chunk.value, { stream: true });
            var frames = pending.split("\n\n");
            pending = frames.pop();
            frames.forEach(function (frame) {
              var line = frame.split("\n").find(function (l) { return l.indexOf("data: ") === 0; });
              if (!line) return;
              var ev;
              try { ev = JSON.parse(line.slice(6)); } catch (e) { return; }
              handle(ev);
            });
            return pump();
          });
        }
        return pump();
      })
      .catch(function (err) {
        if (err.name === "AbortError") {
          ui.status.textContent = "stopped";
        } else {
          showError(String(err.message || err));
        }
        finish();
      });

    function handle(ev) {
      switch (ev.type) {
        case "start":
          ui.status.textContent = ev.backend + " · " + ev.model;
          break;

        case "text":
          buffer += ev.delta;
          ui.answer.innerHTML = md.render(buffer);
          scrollDown();
          break;

        case "thinking":
          ui.status.textContent = "thinking…";
          break;

        case "tool_call": {
          var card = document.createElement("div");
          card.className = "tool-card";
          var args = Object.keys(ev.input || {})
            .filter(function (k) { return ev.input[k] !== null && ev.input[k] !== undefined; })
            .map(function (k) { return k + "=" + JSON.stringify(ev.input[k]); })
            .join(", ");
          card.innerHTML =
            '<button class="tool-row" type="button">' +
            '<span class="tool-icon">⚙</span>' +
            '<span class="tool-name"></span>' +
            '<span class="tool-args"></span>' +
            '<span class="tool-meta"><span class="tool-ms"></span>' +
            '<span class="tool-status pending spin">◌</span></span>' +
            "</button>" +
            '<div class="tool-detail">' +
            "<h4>Arguments</h4><pre class=\"tool-in\"></pre>" +
            '<div class="tool-out-wrap" hidden><h4>Result</h4><pre class="tool-out"></pre></div>' +
            "</div>";
          card.querySelector(".tool-name").textContent = ev.name;
          card.querySelector(".tool-args").textContent = args ? "(" + args + ")" : "()";
          card.querySelector(".tool-in").textContent = JSON.stringify(ev.input || {}, null, 2);
          card.querySelector(".tool-row").addEventListener("click", function () {
            card.classList.toggle("open");
          });
          ui.timeline.appendChild(card);
          cards[ev.id] = card;
          ui.status.textContent = "calling " + ev.name + "…";
          scrollDown();
          break;
        }

        case "tool_result": {
          var target = cards[ev.id];
          if (!target) break;
          var status = target.querySelector(".tool-status");
          status.classList.remove("pending", "spin");
          status.classList.add(ev.ok ? "ok" : "err");
          status.textContent = ev.ok ? "✓" : "✗";
          if (ev.duration_ms) target.querySelector(".tool-ms").textContent = ev.duration_ms + "ms";
          if (ev.preview) {
            var wrap = target.querySelector(".tool-out-wrap");
            wrap.hidden = false;
            target.querySelector(".tool-out").textContent =
              ev.preview + (ev.truncated ? "\n\n[result truncated]" : "");
          }
          break;
        }

        case "report": {
          newReports = true;
          var pill = document.createElement("button");
          pill.className = "report-pill";
          pill.type = "button";
          pill.innerHTML = "📄 <span></span>";
          pill.querySelector("span").textContent = ev.title || ev.path;
          pill.addEventListener("click", function () {
            openReport(ev.path.replace(/^outputs\//, ""));
          });
          ui.reportsRow.appendChild(pill);
          break;
        }

        case "error":
          showError(ev.message);
          break;

        case "done": {
          var bits = [];
          if (ev.turns) bits.push(ev.turns + " turn" + (ev.turns === 1 ? "" : "s"));
          if (ev.tool_calls) bits.push(ev.tool_calls + " tool call" + (ev.tool_calls === 1 ? "" : "s"));
          if (ev.elapsed_ms) bits.push((ev.elapsed_ms / 1000).toFixed(1) + "s");
          var u = ev.usage || {};
          if (u.input_tokens) bits.push(u.input_tokens.toLocaleString() + " in / " + (u.output_tokens || 0).toLocaleString() + " out tokens");
          if (u.cost_usd) bits.push("$" + Number(u.cost_usd).toFixed(4));
          if (ev.note) bits.push(ev.note);
          if (bits.length) {
            ui.foot.hidden = false;
            ui.foot.innerHTML = bits.map(function (b) {
              return "<span>" + md.escapeHtml(b) + "</span>";
            }).join("");
          }
          ui.status.textContent = "answered";
          break;
        }
      }
    }

    function showError(message) {
      var box = document.createElement("div");
      box.className = "error-box";
      box.innerHTML = "<strong>The agent could not complete this request.</strong>";
      var pre = document.createElement("pre");
      pre.textContent = message;
      box.appendChild(pre);
      if (/backend|ANTHROPIC_API_KEY|login/i.test(message)) {
        var hint = document.createElement("p");
        hint.style.margin = "9px 0 0";
        hint.style.fontSize = "12px";
        hint.textContent = "Set ANTHROPIC_API_KEY, or run `claude` in a terminal and /login, then restart the server.";
        box.appendChild(hint);
      }
      ui.answer.appendChild(box);
      ui.status.textContent = "failed";
      scrollDown();
    }

    function finish() {
      ui.answer.classList.remove("streaming");
      setBusy(false);
      state.controller = null;
      if (buffer.trim()) {
        state.history.push({ role: "user", content: question });
        state.history.push({ role: "assistant", content: buffer });
        if (state.history.length > 12) state.history = state.history.slice(-12);
      }
      if (newReports) refreshReports();
      scrollDown();
    }
  }

  boot();
})();
