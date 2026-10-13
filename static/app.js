const $ = (id) => document.getElementById(id);
const round = (n) => Math.round(n * 10) / 10;
const pct = (x) => (x === null ? "–" : Math.round(x * 100) + "%");

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => showTab(tab.dataset.tab));
});

function store(key, value) {
  try {
    localStorage.setItem("cadence-" + key, value);
  } catch {}
}

function recall(key) {
  try {
    return localStorage.getItem("cadence-" + key);
  } catch {
    return null;
  }
}

function remember(username) {
  if (!username) return;
  store("user", username);
  $("li-user").value = username;
  $("t-user").value = username;
}

function showTab(name) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  document.querySelectorAll(".panel").forEach((p) => p.classList.toggle("active", p.id === name));
  store("tab", name);
  if (name === "test" && $("t-user").value) loadStats();
}

function say(el, text, kind = "") {
  el.textContent = text;
  el.className = "msg " + kind;
}

async function post(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return res.json();
}

function recorder(input, strip) {
  const skip = new Set(["Shift", "CapsLock", "Tab", "Enter", "Escape"]);
  let keys, held, start, spoiled;
  function reset() {
    keys = [];
    held = new Map();
    start = null;
    spoiled = false;
    input.value = "";
    draw();
  }
  function draw() {
    if (!strip) return;
    const end = Math.max(1500, ...keys.map((k) => k.up ?? performance.now() - start));
    strip.innerHTML = keys.map((k) => {
      const up = k.up ?? performance.now() - start;
      return `<i style="left:${(k.down / end) * 100}%;width:${((up - k.down) / end) * 100}%"></i>`;
    }).join("");
  }
  input.addEventListener("keydown", (e) => {
    if (e.repeat || skip.has(e.key)) return;
    if (e.key.length !== 1 || e.ctrlKey || e.metaKey || e.altKey) {
      spoiled = true;
      return;
    }
    const now = performance.now();
    if (start === null) start = now;
    const k = { down: now - start, up: null };
    keys.push(k);
    held.set(e.code, k);
    draw();
  });
  input.addEventListener("keyup", (e) => {
    const k = held.get(e.code);
    if (!k) return;
    k.up = performance.now() - start;
    held.delete(e.code);
    draw();
  });
  input.addEventListener("paste", (e) => e.preventDefault());
  input.addEventListener("drop", (e) => e.preventDefault());
  reset();
  return {
    take() {
      const ok = !spoiled && keys.length > 0 && keys.length === input.value.length && keys.every((k) => k.up !== null);
      return ok ? keys.map((k) => [round(k.down), round(k.up)]) : null;
    },
    reset,
  };
}

const TOTAL = Number($("su-dots").dataset.total);
const su = { rec: recorder($("su-pass"), $("su-strip")), samples: [], password: null };

function drawDots() {
  $("su-dots").innerHTML = Array.from({ length: TOTAL }, (_, i) =>
    `<i class="${i < su.samples.length ? "on" : ""}"></i>`
  ).join("");
}

function resetSignup() {
  su.samples = [];
  su.password = null;
  su.rec.reset();
  $("su-pass").disabled = false;
  $("su-create").disabled = true;
  drawDots();
  say($("su-msg"), "No corrections. If you make a typo, just press Enter and redo it.");
}

$("su-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const value = $("su-pass").value;
  const sample = su.rec.take();
  su.rec.reset();
  const msg = $("su-msg");
  if (!sample) return say(msg, "That one had a correction in it. Type it again in one go.", "bad");
  if (su.password === null) {
    if (value.length < 6) return say(msg, "Use at least 6 characters.", "bad");
    su.password = value;
  } else if (value !== su.password) {
    return say(msg, "That doesn't match the first one. Try again.", "bad");
  }
  su.samples.push(sample);
  drawDots();
  const left = TOTAL - su.samples.length;
  if (left > 0) {
    say(msg, `Got it. ${left} more to go. Type it the way you normally would.`);
  } else {
    $("su-pass").disabled = true;
    $("su-create").disabled = false;
    say(msg, "That's all 8. Create your account.", "good");
  }
});

$("su-create").addEventListener("click", async () => {
  const username = $("su-user").value.trim();
  const msg = $("su-msg");
  if (!username) return say(msg, "Pick a username first.", "bad");
  const data = await post("/api/enroll", { username, password: su.password, samples: su.samples });
  if (!data.ok) return say(msg, data.error, "bad");
  const dropped = su.samples.length - data.kept;
  resetSignup();
  remember(username);
  showTab("login");
  const note = dropped ? ` (ignored ${dropped} odd sample${dropped > 1 ? "s" : ""})` : "";
  say($("li-msg"), `Account created${note}. Now try logging in.`, "good");
  $("li-pass").focus();
});

$("su-reset").addEventListener("click", resetSignup);
drawDots();

const liRec = recorder($("li-pass"), $("li-strip"));

$("li-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const password = $("li-pass").value;
  const keys = liRec.take();
  liRec.reset();
  const msg = $("li-msg");
  if (!keys) return say(msg, "No corrections allowed. Type it again in one go.", "bad");
  const username = $("li-user").value.trim();
  const data = await post("/api/login", { username, password, keys });
  if (data.reason === "password" || data.error) {
    $("li-result").hidden = true;
    return say(msg, data.error, "bad");
  }
  remember(username);
  say(msg, "");
  showResult(data);
});

function showResult(data) {
  $("li-result").hidden = false;
  const verdict = $("li-verdict");
  verdict.textContent = data.ok ? "Welcome back. That's you." : "Right password, wrong rhythm.";
  verdict.className = "verdict " + (data.ok ? "good" : "bad");
  const match = Math.max(0, Math.min(100, 100 - (data.distance / data.threshold) * 50));
  $("li-fill").style.width = match + "%";
  $("li-fill").style.background = data.ok ? "var(--good)" : "var(--bad)";
  $("li-numbers").textContent =
    `match ${match.toFixed(0)}%  ·  distance ${data.distance.toFixed(2)}  ·  threshold ${data.threshold.toFixed(2)}`;
  const n = data.length;
  const p = data.profile;
  lineChart($("chart-hold"), {
    labels: Array.from({ length: n }, (_, i) => `${i + 1}`),
    mean: p.mean.slice(0, n),
    spread: p.spread.slice(0, n),
    now: data.attempt.slice(0, n),
  });
  lineChart($("chart-gap"), {
    labels: Array.from({ length: n - 1 }, (_, i) => `${i + 1}→${i + 2}`),
    mean: p.mean.slice(n, 2 * n - 1),
    spread: p.spread.slice(n, 2 * n - 1),
    now: data.attempt.slice(n, 2 * n - 1),
  });
}

const tRec = recorder($("t-pass"), $("t-strip"));
let stats = null;

$("t-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const username = $("t-user").value.trim();
  const password = $("t-pass").value;
  const keys = tRec.take();
  tRec.reset();
  const msg = $("t-msg");
  const label = document.querySelector('input[name="who"]:checked').value;
  if (!username) return say(msg, "Which account are we testing?", "bad");
  if (!keys) return say(msg, "No corrections allowed. Type it again in one go.", "bad");
  const data = await post("/api/test", { username, password, keys, label });
  if (!data.ok) return say(msg, data.error, "bad");
  say(msg, "");
  remember(username);
  loadStats();
});

$("t-user").addEventListener("change", () => {
  remember($("t-user").value.trim());
  loadStats();
});

function drawLog() {
  const t = Number($("s-thr").value);
  $("t-log").innerHTML = stats.recent.map((r) => {
    const who = r.label === "owner" ? "you" : "impostor";
    const inside = r.distance <= t;
    return `<li><span>${who}</span><span>${r.distance.toFixed(2)}</span><span class="${inside ? "yes" : "no"}">${inside ? "let in" : "blocked"}</span></li>`;
  }).join("");
}

async function loadStats() {
  const username = $("t-user").value.trim();
  if (!username) return;
  const res = await fetch("/api/stats?username=" + encodeURIComponent(username));
  const data = await res.json();
  if (!data.ok) return say($("s-msg"), data.error, "bad");
  stats = data;
  $("s-thr").value = data.threshold;
  updateRates();
}

function rates(t) {
  const frr = stats.owner.length ? stats.owner.filter((d) => d > t).length / stats.owner.length : null;
  const far = stats.impostor.length ? stats.impostor.filter((d) => d <= t).length / stats.impostor.length : null;
  return { frr, far };
}

function updateRates() {
  if (!stats) return;
  const t = Number($("s-thr").value);
  const { frr, far } = rates(t);
  $("s-thr-val").textContent = t.toFixed(2);
  $("s-owner").textContent = stats.owner.length;
  $("s-imp").textContent = stats.impostor.length;
  $("s-frr").textContent = pct(frr);
  $("s-far").textContent = pct(far);
  drawLog();
  ratesChart($("chart-rates"), t);
}

$("s-thr").addEventListener("input", updateRates);

$("s-suggest").addEventListener("click", () => {
  if (!stats || !stats.owner.length || !stats.impostor.length) {
    return say($("s-msg"), "Need at least one attempt from you and one from someone else.", "bad");
  }
  let lowest = Infinity;
  let best = [];
  for (let t = 0.5; t <= 6.0001; t += 0.05) {
    const { frr, far } = rates(t);
    const err = Math.round((frr + far) * 1000);
    if (err < lowest) {
      lowest = err;
      best = [t];
    } else if (err === lowest) {
      best.push(t);
    }
  }
  $("s-thr").value = best[Math.floor(best.length / 2)].toFixed(2);
  updateRates();
  say($("s-msg"), "Moved to the threshold with the fewest mistakes. Save it if you like it.");
});

$("s-save").addEventListener("click", async () => {
  const data = await post("/api/threshold", {
    username: $("t-user").value.trim(),
    password: $("s-pw").value,
    threshold: Number($("s-thr").value),
  });
  $("s-pw").value = "";
  if (!data.ok) return say($("s-msg"), data.error, "bad");
  say($("s-msg"), `Saved. Threshold is now ${data.threshold.toFixed(2)}.`, "good");
  loadStats();
});

$("s-clear").addEventListener("click", async () => {
  if (!confirm("Delete all test attempts for this account?")) return;
  const data = await post("/api/tests/clear", {
    username: $("t-user").value.trim(),
    password: $("s-pw").value,
  });
  $("s-pw").value = "";
  if (!data.ok) return say($("s-msg"), data.error, "bad");
  $("t-log").innerHTML = "";
  say($("s-msg"), "Test data cleared.", "good");
  loadStats();
});

function setupCanvas(canvas) {
  const ratio = window.devicePixelRatio || 1;
  const w = canvas.clientWidth;
  if (!canvas.dataset.h) canvas.dataset.h = canvas.getAttribute("height");
  const h = Number(canvas.dataset.h);
  canvas.width = w * ratio;
  canvas.height = h * ratio;
  canvas.style.height = h + "px";
  const ctx = canvas.getContext("2d");
  ctx.scale(ratio, ratio);
  const css = getComputedStyle(document.documentElement);
  const color = (name) => css.getPropertyValue(name).trim();
  return { ctx, w, h, color };
}

function lineChart(canvas, { labels, mean, spread, now }) {
  const { ctx, w, h, color } = setupCanvas(canvas);
  const box = { x: 44, y: 10, w: w - 66, h: h - 36 };
  const top = Math.max(...now, ...mean.map((m, i) => m + spread[i]));
  const low = Math.min(0, ...now, ...mean.map((m, i) => m - spread[i]));
  const step = niceStep((top - low) / 4);
  const yMin = Math.floor(low / step) * step;
  const yMax = Math.ceil(top / step) * step;
  const X = (i) => box.x + (labels.length === 1 ? box.w / 2 : (i / (labels.length - 1)) * box.w);
  const Y = (v) => box.y + box.h - ((v - yMin) / (yMax - yMin)) * box.h;
  ctx.font = "11px JetBrains Mono, monospace";
  ctx.textAlign = "right";
  ctx.textBaseline = "middle";
  for (let v = yMin; v <= yMax + 0.001; v += step) {
    ctx.strokeStyle = color("--line");
    ctx.beginPath();
    ctx.moveTo(box.x, Y(v));
    ctx.lineTo(box.x + box.w, Y(v));
    ctx.stroke();
    ctx.fillStyle = color("--muted");
    ctx.fillText(Math.round(v), box.x - 6, Y(v));
  }
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  const every = Math.ceil(labels.length / 12);
  labels.forEach((l, i) => {
    if (i % every === 0) ctx.fillText(l, X(i), box.y + box.h + 8);
  });
  ctx.fillStyle = color("--accent-soft");
  ctx.beginPath();
  mean.forEach((m, i) => ctx.lineTo(X(i), Y(m + spread[i])));
  for (let i = mean.length - 1; i >= 0; i--) ctx.lineTo(X(i), Y(mean[i] - spread[i]));
  ctx.closePath();
  ctx.fill();
  line(ctx, mean.map((m, i) => [X(i), Y(m)]), color("--accent"), 2);
  line(ctx, now.map((v, i) => [X(i), Y(v)]), color("--now"), 2);
  dots(ctx, now.map((v, i) => [X(i), Y(v)]), color("--now"));
}

function ratesChart(canvas, current) {
  const { ctx, w, h, color } = setupCanvas(canvas);
  const box = { x: 44, y: 10, w: w - 66, h: h - 36 };
  const lo = 0.5, hi = 6;
  const X = (t) => box.x + ((t - lo) / (hi - lo)) * box.w;
  const Y = (r) => box.y + box.h - r * box.h;
  ctx.font = "11px JetBrains Mono, monospace";
  ctx.textAlign = "right";
  ctx.textBaseline = "middle";
  for (let i = 0; i <= 4; i++) {
    const v = i / 4;
    ctx.strokeStyle = color("--line");
    ctx.beginPath();
    ctx.moveTo(box.x, Y(v));
    ctx.lineTo(box.x + box.w, Y(v));
    ctx.stroke();
    ctx.fillStyle = color("--muted");
    ctx.fillText(Math.round(v * 100) + "%", box.x - 6, Y(v));
  }
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  for (let t = 1; t <= hi; t++) ctx.fillText(t, X(t), box.y + box.h + 8);
  if (!stats) return;
  const frr = [], far = [];
  for (let t = lo; t <= hi + 0.0001; t += 0.05) {
    const r = rates(t);
    if (r.frr !== null) frr.push([X(t), Y(r.frr)]);
    if (r.far !== null) far.push([X(t), Y(r.far)]);
  }
  line(ctx, frr, color("--accent"), 2);
  line(ctx, far, color("--now"), 2);
  ctx.strokeStyle = color("--ink");
  ctx.setLineDash([4, 4]);
  ctx.beginPath();
  ctx.moveTo(X(current), box.y);
  ctx.lineTo(X(current), box.y + box.h);
  ctx.stroke();
  ctx.setLineDash([]);
}

function line(ctx, points, stroke, width) {
  if (!points.length) return;
  ctx.strokeStyle = stroke;
  ctx.lineWidth = width;
  ctx.lineJoin = "round";
  ctx.beginPath();
  points.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
  ctx.stroke();
  ctx.lineWidth = 1;
}

function dots(ctx, points, fill) {
  ctx.fillStyle = fill;
  points.forEach(([x, y]) => {
    ctx.beginPath();
    ctx.arc(x, y, 3, 0, Math.PI * 2);
    ctx.fill();
  });
}

function niceStep(raw) {
  const mag = Math.pow(10, Math.floor(Math.log10(Math.max(raw, 1))));
  const n = raw / mag;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * mag;
}

window.addEventListener("resize", () => {
  if (stats) updateRates();
});

remember(recall("user"));
showTab(recall("tab") || "signup");
