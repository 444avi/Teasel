const state = { snapshot: null, lastSideIndex: null, requestId: 0 };
const $ = (selector) => document.querySelector(selector);

function money(cents, signed = false) {
  if (cents === null || cents === undefined) return "—";
  const value = Number(cents) / 100;
  const prefix = signed && value > 0 ? "+" : "";
  return `${prefix}${value < 0 ? "-" : ""}$${Math.abs(value).toFixed(2)}`;
}

function pct(value) {
  return value === null || value === undefined ? "—" : `${(Number(value) * 100).toFixed(0)}%`;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[char]));
}

function rowMarkup(rung, index) {
  const threshold = Number(rung.threshold).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
  return `<tr data-index="${index}">
    <td><span class="threshold-main">${rung.operator === ">=" ? "≥" : ">"} $${threshold}</span><span class="threshold-sub">${escapeHtml(rung.status)}</span></td>
    <td><span class="ask-price">${rung.yes_ask_cents == null ? "—" : `${rung.yes_ask_cents}¢`}</span></td>
    <td><span class="ask-price">${rung.no_ask_cents == null ? "—" : `${rung.no_ask_cents}¢`}</span></td>
    <td class="prob-cell">${pct(rung.probability_above)}</td>
    <td><div class="position-control">
      <button class="side-button yes" data-side="YES" aria-label="Buy YES at ${threshold}">YES</button>
      <button class="side-button no" data-side="NO" aria-label="Buy NO at ${threshold}">NO</button>
    </div></td>
    <td><input class="qty-input" type="number" value="0" min="0" step="1" aria-label="Quantity at ${threshold}"></td>
  </tr>`;
}

function renderSnapshot(snapshot) {
  state.snapshot = snapshot;
  state.lastSideIndex = null;
  $("#event-title").textContent = snapshot.title;
  $("#event-ticker").textContent = snapshot.event_ticker;
  $("#rung-count").textContent = `${snapshot.rungs.length} rungs`;
  $("#event-mode").textContent = snapshot.is_demo ? "Demo data" : "Live snapshot";
  $("#ladder-body").innerHTML = snapshot.rungs.map(rowMarkup).join("");
  const warnings = $("#warnings");
  warnings.hidden = !snapshot.warnings?.length;
  warnings.textContent = (snapshot.warnings || []).join(" ");
  $("#workspace").classList.remove("is-loading");
  bindRows();
  analyze();
}

function setSide(index, side, keepQty = false) {
  const row = $(`#ladder-body tr[data-index="${index}"]`);
  if (!row) return;
  const alreadyActive = row.querySelector(`[data-side="${side}"]`).classList.contains("active");
  row.querySelectorAll(".side-button").forEach(button => button.classList.remove("active"));
  row.classList.remove("selected");
  if (alreadyActive && !keepQty) {
    row.querySelector(".qty-input").value = 0;
    return;
  }
  row.querySelector(`[data-side="${side}"]`).classList.add("active");
  row.classList.add("selected");
  const qty = row.querySelector(".qty-input");
  if (Number(qty.value) < 1) qty.value = 1;
}

function bindRows() {
  document.querySelectorAll(".side-button").forEach(button => {
    button.addEventListener("click", event => {
      const row = event.currentTarget.closest("tr");
      const index = Number(row.dataset.index);
      const side = event.currentTarget.dataset.side;
      if (event.shiftKey && state.lastSideIndex !== null) {
        const [start, end] = [state.lastSideIndex, index].sort((a, b) => a - b);
        for (let i = start; i <= end; i++) setSide(i, side, true);
      } else {
        setSide(index, side);
      }
      state.lastSideIndex = index;
      analyze();
    });
  });
  document.querySelectorAll(".qty-input").forEach(input => {
    input.addEventListener("input", () => {
      const row = input.closest("tr");
      if (Number(input.value) <= 0) {
        row.querySelectorAll(".side-button").forEach(button => button.classList.remove("active"));
        row.classList.remove("selected");
      } else if (!row.querySelector(".side-button.active")) {
        setSide(Number(row.dataset.index), "YES", true);
      }
      analyze();
    });
  });
}

function analysisPayload() {
  return {
    use_maker: $("#maker-toggle").checked,
    fee_rate: Number($("#fee-rate").value || .07),
    rungs: state.snapshot.rungs.map((rung, index) => {
      const row = $(`#ladder-body tr[data-index="${index}"]`);
      const active = row.querySelector(".side-button.active");
      return {...rung, side: active?.dataset.side || null, qty: Number(row.querySelector(".qty-input").value || 0)};
    })
  };
}

async function analyze() {
  if (!state.snapshot) return;
  const requestId = ++state.requestId;
  try {
    const response = await fetch("/api/analyze", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(analysisPayload())});
    const result = await response.json();
    if (requestId !== state.requestId) return;
    if (!response.ok) throw new Error(result.error || "Analysis failed");
    renderAnalysis(result);
  } catch (error) {
    $("#fetch-message").textContent = error.message;
  }
}

function renderAnalysis(result) {
  $("#metric-cost").textContent = money(result.total_cost_cents);
  $("#metric-fees").textContent = `incl. ${money(result.total_fees_cents)} fees`;
  $("#metric-risk").textContent = money(result.max_loss_cents);
  $("#metric-gain").textContent = money(result.max_gain_cents, true);
  $("#metric-profit-prob").textContent = pct(result.profit_probability);
  $("#metric-ev").textContent = money(result.market_ev_cents, true);

  const alarm = result.arbitrage_alarm || !result.monotonicity_ok;
  const badge = $("#guard-badge");
  badge.className = `guard-badge ${alarm ? "bad" : "good"}`;
  badge.innerHTML = `<i></i> ${alarm ? "Review flags" : "Coherent"}`;
  const guards = $("#guards");
  guards.className = `guards ${alarm ? "alarm" : ""}`;
  guards.innerHTML = `<span class="guard-icon">${alarm ? "!" : "✓"}</span><p>${result.guard_messages.length ? result.guard_messages.map(escapeHtml).join("<br>") : "No coherence flags in this snapshot."}</p>`;
  $(".metric.ev").classList.toggle("alarm", result.market_ev_cents > 0);
  renderChart(result.bins, result.selected_leg_count);
}

function displayThreshold(value) {
  return Number(value).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
}

function payoffRangeLabel(firstBin, lastBin) {
  const lower = firstBin.bin.lower;
  const upper = lastBin.bin.upper;
  if (lower === null || lower === undefined) return `≤ $${displayThreshold(upper)}`;
  if (upper === null || upper === undefined) return `≥ $${displayThreshold(lower)}`;
  return `($${displayThreshold(lower)}, $${displayThreshold(upper)}]`;
}

function compactPayoffBins(bins) {
  const groups = [];
  bins.forEach(bin => {
    const current = groups[groups.length - 1];
    if (current && current.net_cents === bin.net_cents) {
      current.last = bin;
      current.binCount += 1;
      if (current.probability !== null && bin.probability !== null) current.probability += bin.probability;
      else current.probability = null;
      return;
    }
    groups.push({...bin, first: bin, last: bin, binCount: 1});
  });
  return groups.map(group => ({
    ...group,
    bin: {...group.bin, label: payoffRangeLabel(group.first, group.last)}
  }));
}

function renderChart(bins, legCount) {
  const svg = $("#payoff-chart");
  const empty = $("#chart-empty");
  const strip = $("#bin-strip");
  const displayBins = compactPayoffBins(bins);
  strip.style.gridTemplateColumns = `repeat(${displayBins.length}, minmax(0, 1fr))`;
  strip.innerHTML = displayBins.map(item => `<div class="bin-item ${item.net_cents > 0 ? "gain" : item.net_cents < 0 ? "loss" : ""}" title="${escapeHtml(item.bin.label)}"><span>${escapeHtml(item.bin.label)}</span><strong>${money(item.net_cents, true)}</strong></div>`).join("");
  if (!legCount) {
    svg.innerHTML = "";
    empty.hidden = false;
    return;
  }
  empty.hidden = true;
  const width = 620, height = 250, pad = {l: 49, r: 18, t: 20, b: 20};
  const values = displayBins.map(item => Number(item.net_cents));
  let min = Math.min(0, ...values), max = Math.max(0, ...values);
  const range = Math.max(50, max - min);
  min -= range * .18; max += range * .18;
  const x = index => pad.l + index * (width - pad.l - pad.r) / displayBins.length;
  const y = value => pad.t + (max - value) * (height - pad.t - pad.b) / (max - min);
  const zeroY = y(0);
  let stepPath = `M ${x(0)} ${y(values[0])}`;
  values.forEach((value, index) => {
    stepPath += ` H ${x(index + 1)}`;
    if (index < values.length - 1) stepPath += ` V ${y(values[index + 1])}`;
  });
  const ticks = [max, (max + min) / 2, min];
  const grids = ticks.map(value => `<line class="chart-grid" x1="${pad.l}" y1="${y(value)}" x2="${width-pad.r}" y2="${y(value)}"/><text class="chart-axis-label" x="${pad.l-7}" y="${y(value)+3}" text-anchor="end">${money(Math.round(value))}</text>`).join("");
  const fills = values.map((value, index) => {
    const top = Math.min(y(value), zeroY), h = Math.abs(zeroY - y(value));
    return `<rect class="${value >= 0 ? "chart-fill-gain" : "chart-fill-loss"}" x="${x(index)}" y="${top}" width="${x(index+1)-x(index)}" height="${h}"/>`;
  }).join("");
  const points = values.map((value, index) => `<circle class="chart-point" cx="${(x(index)+x(index+1))/2}" cy="${y(value)}" r="4" fill="${value >= 0 ? "#7ba21e" : "#f16f5c"}"/>`).join("");
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.innerHTML = `${fills}${grids}<line class="chart-zero" x1="${pad.l}" y1="${zeroY}" x2="${width-pad.r}" y2="${zeroY}"/><path class="chart-step" d="${stepPath}" stroke="#17233a"/>${points}`;
}

async function fetchSnapshot() {
  const event = $("#event-input").value.trim();
  if (!event) { $("#fetch-message").textContent = "Paste a Kalshi event link or ticker first."; return; }
  const button = $("#fetch-button");
  button.disabled = true;
  button.firstChild.textContent = "Fetching… ";
  $("#fetch-message").textContent = "Reading the current public snapshot…";
  try {
    const response = await fetch("/api/fetch", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({event, probability_source: $("#probability-source").value})});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not load this event");
    $("#fetch-message").textContent = "";
    renderSnapshot(result);
  } catch (error) {
    $("#fetch-message").textContent = error.message;
  } finally {
    button.disabled = false;
    button.firstChild.textContent = "Fetch ladder ";
  }
}

$("#fetch-button").addEventListener("click", fetchSnapshot);
$("#event-input").addEventListener("keydown", event => { if (event.key === "Enter") fetchSnapshot(); });
$("#maker-toggle").addEventListener("change", analyze);
$("#fee-rate").addEventListener("input", analyze);

fetch("/api/demo").then(response => response.json()).then(snapshot => {
  renderSnapshot(snapshot);
  // Seed the pinned acceptance position so the first view demonstrates the payoff comb.
  [[0,"NO"],[1,"YES"],[2,"NO"],[3,"YES"]].forEach(([index, side]) => setSide(index, side, true));
  analyze();
}).catch(() => { $("#fetch-message").textContent = "Demo data could not be loaded."; });
