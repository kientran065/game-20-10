(() => {
  const $ = (s) => document.querySelector(s);
  const views = ["home", "lobby", "play", "results"];
  let session = JSON.parse(sessionStorage.getItem("session") || "null"); // {code, pid}
  let state = null, poller = null, lastRound = 0, countdownTotal = 0;

  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

  function toast(msg) {
    const t = $("#toast");
    t.textContent = msg; t.hidden = false;
    clearTimeout(toast.id);
    toast.id = setTimeout(() => (t.hidden = true), 3200);
  }

  async function api(path, body) {
    const opts = body !== undefined
      ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }
      : {};
    const res = await fetch(path, opts);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw Object.assign(new Error(data.error || "Có lỗi xảy ra."), { status: res.status });
    return data;
  }
  const withSession = (extra = {}) => ({ code: session.code, pid: session.pid, ...extra });

  function show(name) {
    views.forEach((v) => ($("#view-" + v).hidden = v !== name));
  }

  function saveSession(s) {
    session = s;
    sessionStorage.setItem("session", JSON.stringify(s));
    startPolling();
  }
  function clearSession() {
    session = null; state = null; lastRound = 0;
    sessionStorage.removeItem("session");
    clearInterval(poller); poller = null;
    $("#roomTag").hidden = true;
    show("home");
  }

  // ---------- Trang chủ
  $("#createForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    try {
      const r = await api("/api/create", {
        name: f.get("name"), max_players: f.get("max_players"),
        time_limit: f.get("time_limit"), early_end: f.get("early_end") === "on",
      });
      saveSession(r);
    } catch (err) { toast(err.message); }
  });
  $("#joinForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    try {
      const r = await api("/api/join", { name: f.get("name"), code: f.get("code") });
      saveSession(r);
    } catch (err) { toast(err.message); }
  });
  $("#leaveBtn").addEventListener("click", async () => {
    if (!confirm("Rời phòng? Nếu bạn là quản trò, phòng sẽ bị đóng.")) return;
    try { await api("/api/leave", withSession()); } catch {}
    clearSession();
  });

  // ---------- Phòng chờ
  $("#startBtn").addEventListener("click", async () => {
    try { await api("/api/start", withSession()); poll(); } catch (err) { toast(err.message); }
  });
  $("#settingsForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    try {
      await api("/api/settings", withSession({
        max_players: f.get("max_players"), time_limit: f.get("time_limit"),
        early_end: f.get("early_end") === "on",
      }));
      toast("Đã lưu cài đặt."); poll();
    } catch (err) { toast(err.message); }
  });
  let settingsDirty = false;
  $("#settingsForm").addEventListener("input", () => (settingsDirty = true));
  $("#settingsForm").addEventListener("submit", () => (settingsDirty = false));

  // ---------- Đang chơi
  $("#pickForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const n = parseInt($("#pickInput").value, 10);
    if (!(n >= 1 && n <= 99)) return toast("Hãy nhập số nguyên từ 1 đến 99.");
    try { await api("/api/submit", withSession({ number: n })); poll(); }
    catch (err) { toast(err.message); }
  });

  $("#restartBtn").addEventListener("click", async () => {
    try { await api("/api/restart", withSession()); poll(); } catch (err) { toast(err.message); }
  });

  // ---------- Vẽ giao diện theo trạng thái
  function render(s) {
    state = s;
    $("#roomTag").hidden = false;
    $("#roomCode").textContent = s.code;
    const host = s.me.is_host;

    if (s.status === "lobby") renderLobby(s, host);
    else if (s.status === "playing") renderPlay(s);
    else renderResults(s, host);
  }

  function renderLobby(s, host) {
    show("lobby");
    $("#bigCode").textContent = s.code;
    $("#playerCount").textContent = `${s.players.length}/${s.settings.max_players}`;
    $("#lobbyPlayers").innerHTML = s.players.map((p) =>
      `<li><span>${esc(p.name)}${p.id === s.me.id ? " (bạn)" : ""}</span>${p.is_host ? '<span class="tag">Quản trò</span>' : ""}</li>`).join("");
    $("#startBtn").hidden = !host;
    $("#startBtn").disabled = s.players.length < 2;
    $("#startBtn").textContent = s.round === 0 ? "Bắt đầu" : "Bắt đầu vòng mới";
    $("#waitMsg").hidden = host;

    const form = $("#settingsForm");
    form.querySelectorAll("input").forEach((i) => (i.disabled = !host));
    $("#saveSettings").hidden = !host;
    $("#settingsReadonly").hidden = host;
    if (!settingsDirty || !host) {
      form.max_players.value = s.settings.max_players;
      form.time_limit.value = s.settings.time_limit;
      form.early_end.checked = s.settings.early_end;
    }
  }

  function renderPlay(s) {
    show("play");
    if (s.round !== lastRound) {   // vòng mới: đặt lại ô nhập
      lastRound = s.round;
      $("#pickInput").value = "";
      countdownTotal = s.settings.time_limit;
      setTimeout(() => $("#pickInput").focus(), 50);
    }
    $("#roundNo").textContent = s.round;
    const done = s.me.pick !== null;
    $("#pickInput").disabled = done;
    $("#pickBtn").disabled = done;
    $("#pickedMsg").hidden = !done;
    if (done) {
      $("#pickInput").value = s.me.pick;
      $("#pickedMsg").innerHTML = `Bạn đã chốt số <b>${s.me.pick}</b>. Đang chờ những người còn lại…`;
    }
    const t = Math.ceil(s.remaining);
    $("#timer").textContent = t;
    $("#timer").classList.toggle("low", t <= 5);
    $("#barFill").style.width = Math.max(0, (s.remaining / countdownTotal) * 100) + "%";
    const n = s.players.filter((p) => p.submitted).length;
    $("#submittedCount").textContent = `${n}/${s.players.length}`;
    $("#playPlayers").innerHTML = s.players.map((p) =>
      `<li><span>${esc(p.name)}${p.id === s.me.id ? " (bạn)" : ""}</span>` +
      `<span class="tag ${p.submitted ? "ok" : ""}">${p.submitted ? "Đã chốt" : "Đang nghĩ…"}</span></li>`).join("");
  }

  function renderResults(s, host) {
    show("results");
    const r = s.result;
    const mine = r.my_pick;
    const iWon = r.winner_ids.includes(s.me.id);

    if (mine === null) $("#verdict").textContent = "Bạn chưa chốt số kịp lúc, nên không được tính vòng này.";
    else if (iWon) $("#verdict").textContent = r.winner_ids.length > 1
      ? `Bạn thắng, chia ${r.gain[s.me.id]} điểm với ${r.winner_ids.length - 1} người khác.`
      : "Bạn thắng vòng này: 100 điểm.";
    else $("#verdict").textContent = r.winners.length ? `${r.winners.join(", ")} thắng vòng này.` : "Không ai chốt số.";

    $("#mPick").textContent = mine ?? "–";
    $("#mTarget").textContent = r.target ?? "–";
    $("#mClosest").textContent = r.closest.length ? r.closest.join(" / ") : "–";

    // Biểu đồ cột
    const max = Math.max(1, ...r.histogram.map((h) => h.count));
    $("#chart").innerHTML = r.histogram.map((h, i) => {
      const lo = i * 10 + 1, hi = i === 9 ? 99 : (i + 1) * 10;
      const isMine = mine !== null && mine >= lo && mine <= hi;
      return `<div class="col ${isMine ? "mine" : ""}"><span class="n">${h.count}</span>` +
        `<div class="b" style="height:${(h.count / max) * 80}%"></div><span class="x">${h.label}</span></div>`;
    }).join("");

    // Bảng thành tích
    const rows = [...s.players].sort((a, b) => b.score - a.score);
    $("#boardBody").innerHTML = rows.map((p) => {
      const g = r.gain[p.id];
      return `<tr class="${r.winner_ids.includes(p.id) ? "win" : ""}"><td>${esc(p.name)}${p.id === s.me.id ? " (bạn)" : ""}</td>` +
        `<td>${p.score}${g ? `<span class="gain">+${g}</span>` : ""}</td></tr>`;
    }).join("");

    $("#restartBtn").hidden = !host;
    $("#restartWait").hidden = host;
  }

  // ---------- Đồng bộ với máy chủ
  async function poll() {
    if (!session) return;
    try { render(await api(`/api/state?code=${session.code}&pid=${session.pid}`)); }
    catch (err) {
      if (err.status === 404) { toast("Phòng đã đóng hoặc không còn tồn tại."); clearSession(); }
    }
  }
  function startPolling() {
    clearInterval(poller);
    poll();
    poller = setInterval(poll, 1000);
  }

  if (session) startPolling(); else show("home");
})();
