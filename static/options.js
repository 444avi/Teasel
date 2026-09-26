const state = {
  legs: [
    {kind: "CALL", direction: "LONG", qty: "1", strike: "100", premium: "6.00", commission: ""},
    {kind: "CALL", direction: "SHORT", qty: "1", strike: "110", premium: "2.00", commission: ""},
  ],
  requestId: 0,
  timer: null,
};

const KIND_LABELS = {CALL: "Call", PUT: "Put", STOCK: "Stock"};

function isComplete(leg) {
  return leg.qty.trim() !== "" && leg.premium.trim() !== "" && (leg.kind === "STOCK" || leg.strike.trim() !== "");
}

function legRowMarkup(leg, index) {
  const stock = leg.kind === "STOCK";
  const kinds = Object.entries(KIND_LABELS).map(([value, label]) =>
    `<option value="${value}" ${leg.kind === value ? "selected" : ""}>${label}</option>`).join("");
  return `<tr data-index="${index}">
    <td><select class="leg-select" data-field="kind" aria-label="Leg ${index + 1} type">${kinds}</select></td>
    <td><div class="position-control">
      <button type="button" class="side-button yes ${leg.direction === "LONG" ? "active" : ""}" data-direction="LONG" aria-pressed="${leg.direction === "LONG"}">Long</button>
      <button type="button" class="side-button no ${leg.direction === "SHORT" ? "active" : ""}" data-direction="SHORT" aria-pressed="${leg.direction === "SHORT"}">Short</button>
    </div></td>
    <td><input class="leg-input qty" data-field="qty" type="number" min="1" step="1" value="${escapeHtml(leg.qty)}" aria-label="Leg ${index + 1} ${stock ? "shares" : "contracts"}"><span class="leg-sub">${stock ? "shares" : "contracts"}</span></td>
    <td><input class="leg-input" data-field="strike" type="number" min="0.01" step="0.01" value="${stock ? "" : escapeHtml(leg.strike)}" ${stock ? "disabled" : ""} aria-label="Leg ${index + 1} strike"></td>
    <td><input class="leg-input" data-field="premium" type="number" min="0" step="0.01" value="${escapeHtml(leg.premium)}" placeholder="0.00" aria-label="Leg ${index + 1} ${stock ? "entry price per share" : "premium per share"}"><span class="leg-sub">${stock ? "Entry $/sh" : "$/sh"}</span></td>
    <td class="commission-col"><input class="leg-input" data-field="commission" type="number" min="0" step="0.01" value="${escapeHtml(leg.commission)}" placeholder="0.00" aria-label="Leg ${index + 1} commission"></td>
    <td><button type="button" class="leg-remove" aria-label="Remove leg ${index + 1}">×</button></td>
  </tr>`;
}

function renderLegs() {
  $("#legs-body").innerHTML = state.legs.map(legRowMarkup).join("");
}

function renderLegRow(index) {
  const row = $(`#legs-body tr[data-index="${index}"]`);
  if (row) row.outerHTML = legRowMarkup(state.legs[index], index);
}

function scheduleAnalyze() {
  clearTimeout(state.timer);
  state.timer = setTimeout(analyze, 150);
}

function legPayload(leg) {
  const payload = {
    kind: leg.kind,
    direction: leg.direction,
    qty: leg.qty.trim(),
    premium: leg.premium.trim(),
    multiplier: $("#multiplier-input").value.trim() || "100",
  };
  if (leg.kind !== "STOCK") payload.strike = leg.strike.trim();
  if ($("#commission-toggle").checked && leg.commission.trim() !== "") payload.commission = leg.commission.trim();
  return payload;
}

async function analyze() {
  clearTimeout(state.timer);
  const requestId = ++state.requestId;
  const sent = [];
  state.legs.forEach((leg, index) => { if (isComplete(leg)) sent.push(index); });
  if (!sent.length) {
    renderEmpty();
    return;
  }
  const spot = $("#spot-input").value.trim();
  try {
    const response = await fetch("/api/options/analyze", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({spot: spot || null, legs: sent.map(index => legPayload(state.legs[index]))}),
    });
    const result = await apiJson(response);
    if (requestId !== state.requestId) return;
    const skipped = state.legs.length - sent.length;
    $("#options-message").textContent = skipped ? `${skipped} incomplete ${skipped === 1 ? "leg is" : "legs are"} not included yet.` : "";
    renderResult(result, sent);
  } catch (error) {
    if (requestId !== state.requestId) return;
    $("#options-message").textContent = error.message;
  }
}

function renderEmpty() {
  $("#options-chart").innerHTML = "";
  $("#options-chart-empty").hidden = false;
  ["#metric-premium", "#metric-max-loss", "#metric-max-profit", "#metric-breakevens", "#metric-spot-pl"].forEach(id => { $(id).textContent = "—"; });
  $("#metric-commissions").textContent = "+ $0.00 commissions";
  const badge = $("#options-guard-badge");
  badge.className = "guard-badge good";
  badge.innerHTML = "<i></i> No flags";
  const guards = $("#options-guards");
  guards.className = "guards";
  guards.innerHTML = `<span class="guard-icon">✓</span><p>No flags for this position.</p>`;
  $("#options-message").textContent = state.legs.length ? "Complete a leg to map payoff." : "";
  document.querySelectorAll("#legs-body tr").forEach(row => row.classList.remove("flagged"));
}

function price(value) {
  const number = Number(value);
  const digits = Number.isInteger(Math.round(number * 10000) / 100) ? 2 : 4;
  return `$${number.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: digits})}`;
}

function breakevenText(result) {
  const items = result.breakevens.map(value => ({start: value, text: price(value)}));
  result.breakeven_ranges.forEach(([start, end]) => {
    items.push({start, text: end === null ? `${price(start)} and above` : `${price(start)} to ${price(end)}`});
  });
  if (!items.length) return "None";
  return items.sort((a, b) => a.start - b.start).map(item => item.text).join(", ");
}

function renderResult(result, sent) {
  const net = result.net_premium_cents;
  $("#metric-premium").innerHTML = net === 0 ? "$0.00" : `${escapeHtml(money(Math.abs(net)))} <span class="premium-side">${net < 0 ? "debit" : "credit"}</span>`;
  $("#metric-commissions").textContent = `+ ${money(result.commissions_cents)} commissions`;
  $("#metric-max-loss").innerHTML = result.max_loss_unlimited ? `<span class="unlimited">Unlimited</span>` : escapeHtml(money(result.max_loss_cents));
  $("#metric-max-profit").textContent = result.max_profit_unlimited ? "Unlimited" : money(result.max_profit_cents, true);
  $("#metric-breakevens").textContent = breakevenText(result);
  $("#metric-spot-pl").textContent = result.spot === null ? "—" : money(result.pl_at_spot_cents, true);
  $("#metric-spot-note").textContent = result.spot === null ? "enter a spot price" : `if it settles at ${price(result.spot)}`;
  renderGuards(result.guards, sent);
  renderChart(result);
}

function renderGuards(guards, sent) {
  const warning = guards.some(guard => guard.severity === "warning");
  const badge = $("#options-guard-badge");
  badge.className = `guard-badge ${warning ? "bad" : guards.length ? "info" : "good"}`;
  badge.innerHTML = `<i></i> ${warning ? "Review flags" : guards.length ? "Notes" : "No flags"}`;
  const box = $("#options-guards");
  box.className = `guards ${warning ? "alarm" : guards.length ? "info" : ""}`;
  box.innerHTML = guards.length
    ? `<ul class="guard-list">${guards.map(guard => `<li class="${escapeHtml(guard.severity)}" data-code="${escapeHtml(guard.code)}"><span class="guard-icon">${guard.severity === "warning" ? "!" : "i"}</span><p>${escapeHtml(guard.message)}</p></li>`).join("")}</ul>`
    : `<span class="guard-icon">✓</span><p>No flags for this position.</p>`;
  const flagged = new Set();
  guards.filter(guard => guard.severity === "warning").forEach(guard => guard.leg_indexes.forEach(index => flagged.add(sent[index])));
  document.querySelectorAll("#legs-body tr").forEach(row => row.classList.toggle("flagged", flagged.has(Number(row.dataset.index))));
}

function renderChart(result) {
  const svg = $("#options-chart");
  $("#options-chart-empty").hidden = true;
  const width = 620, height = 250, pad = {l: 49, r: 18, t: 20, b: 20};
  const [lo, hi] = result.price_range.map(Number);
  const vertices = result.vertices.map(([x, pl]) => [Number(x), Number(pl)]);
  const values = vertices.map(([, pl]) => pl);
  let min = Math.min(0, ...values), max = Math.max(0, ...values);
  const range = Math.max(50, max - min);
  min -= range * .18; max += range * .18;
  const x = value => pad.l + (value - lo) * (width - pad.l - pad.r) / (hi - lo);
  const y = value => pad.t + (max - value) * (height - pad.t - pad.b) / (max - min);
  const zeroY = y(0), top = pad.t, bottom = height - pad.b;

  const fills = vertices.slice(1).map(([x2, p2], index) => {
    const [x1, p1] = vertices[index];
    const cls = p1 >= 0 && p2 >= 0 ? "chart-fill-gain" : "chart-fill-loss";
    return `<polygon class="${cls}" points="${x(x1)},${y(p1)} ${x(x2)},${y(p2)} ${x(x2)},${zeroY} ${x(x1)},${zeroY}"/>`;
  }).join("");
  const ticks = [max, (max + min) / 2, min];
  const grids = ticks.map(value => `<line class="chart-grid" x1="${pad.l}" y1="${y(value)}" x2="${width-pad.r}" y2="${y(value)}"/><text class="chart-axis-label" x="${pad.l-7}" y="${y(value)+3}" text-anchor="end">${money(Math.round(value))}</text>`).join("");
  const strikes = result.strikes.map(Number).filter(value => value >= lo && value <= hi).map(value =>
    `<line class="chart-strike-tick" x1="${x(value)}" y1="${bottom - 4}" x2="${x(value)}" y2="${bottom + 2}"/><text class="chart-strike" x="${x(value)}" y="${height + 10}" text-anchor="middle">$${escapeHtml(value.toLocaleString(undefined, {maximumFractionDigits: 2}))}</text>`).join("");
  const breakevens = result.breakevens.map(Number).filter(value => value > lo && value < hi).map(value =>
    `<line class="chart-breakeven" x1="${x(value)}" y1="${top}" x2="${x(value)}" y2="${bottom}"/><text class="chart-axis-label" x="${x(value)}" y="${top - 7}" text-anchor="middle">BE ${escapeHtml(price(value))}</text>`).join("");
  const spot = result.spot === null || Number(result.spot) < lo || Number(result.spot) > hi ? "" :
    `<line class="chart-spot" x1="${x(Number(result.spot))}" y1="${top}" x2="${x(Number(result.spot))}" y2="${bottom}"/><text class="chart-axis-label chart-spot-label" x="${x(Number(result.spot)) - 4}" y="${bottom - 6}" text-anchor="end">spot</text>`;
  const lastY = y(values[values.length - 1]);
  const unlimited = [];
  if (result.max_profit_unlimited) unlimited.push(`<text class="chart-axis-label chart-unlimited" x="${width - pad.r}" y="${lastY + 14}" text-anchor="end">↗ unlimited</text>`);
  if (result.max_loss_unlimited) unlimited.push(`<text class="chart-axis-label chart-unlimited" x="${width - pad.r}" y="${lastY - 8}" text-anchor="end">↘ unlimited</text>`);
  const line = vertices.map(([value, pl]) => `${x(value)},${y(pl)}`).join(" ");

  svg.setAttribute("viewBox", `0 0 ${width} ${height + 12}`);
  svg.innerHTML = `${fills}${grids}<line class="chart-zero" x1="${pad.l}" y1="${zeroY}" x2="${width-pad.r}" y2="${zeroY}"/>${breakevens}${spot}<polyline class="chart-line" points="${line}"/>${strikes}${unlimited.join("")}`;
}

async function loadPresets() {
  const select = $("#preset-select");
  try {
    const result = await apiJson(await fetch("/api/options/presets"));
    select.innerHTML = result.presets.map(preset => `<option value="${escapeHtml(preset.name)}">${escapeHtml(preset.label)}</option>`).join("");
  } catch (error) {
    select.innerHTML = `<option value="">Presets unavailable</option>`;
    $("#options-message").textContent = error.message;
  }
}

async function applyPreset() {
  const name = $("#preset-select").value;
  const spot = $("#spot-input").value.trim();
  if (!name) return;
  if (!spot) { $("#options-message").textContent = "Enter a spot price to apply a preset."; return; }
  const button = $("#preset-button");
  button.disabled = true;
  try {
    const response = await fetch("/api/options/preset", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({name, spot, width: $("#width-input").value.trim(), qty: $("#preset-qty").value.trim()}),
    });
    const result = await apiJson(response);
    state.legs = result.legs.map(leg => ({
      kind: leg.kind,
      direction: leg.direction,
      qty: String(leg.qty),
      strike: leg.strike === null ? "" : String(leg.strike),
      premium: leg.kind === "STOCK" ? String(leg.premium) : "0.00",
      commission: "",
    }));
    $("#options-message").textContent = "";
    renderLegs();
    analyze();
  } catch (error) {
    $("#options-message").textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

$("#legs-body").addEventListener("input", event => {
  const field = event.target.dataset.field;
  const row = event.target.closest("tr");
  if (!field || !row) return;
  const index = Number(row.dataset.index);
  state.legs[index][field] = event.target.value;
  if (field === "kind") {
    if (event.target.value === "STOCK") state.legs[index].strike = "";
    renderLegRow(index);
  }
  scheduleAnalyze();
});

$("#legs-body").addEventListener("click", event => {
  const row = event.target.closest("tr");
  if (!row) return;
  const index = Number(row.dataset.index);
  const side = event.target.closest(".side-button");
  if (side) {
    state.legs[index].direction = side.dataset.direction;
    renderLegRow(index);
    scheduleAnalyze();
  } else if (event.target.closest(".leg-remove")) {
    state.legs.splice(index, 1);
    renderLegs();
    scheduleAnalyze();
  }
});

$("#add-leg-button").addEventListener("click", () => {
  const last = state.legs[state.legs.length - 1];
  state.legs.push({kind: "CALL", direction: "LONG", qty: "1", strike: last && last.kind !== "STOCK" ? last.strike : "", premium: "", commission: ""});
  renderLegs();
  const inputs = document.querySelectorAll(`#legs-body tr[data-index="${state.legs.length - 1}"] [data-field="premium"]`);
  if (inputs.length) inputs[0].focus();
  scheduleAnalyze();
});

$("#commission-toggle").addEventListener("change", event => {
  $("#legs-table").classList.toggle("show-commission", event.target.checked);
  scheduleAnalyze();
});
$("#multiplier-input").addEventListener("input", scheduleAnalyze);
$("#spot-input").addEventListener("input", scheduleAnalyze);
$("#preset-button").addEventListener("click", applyPreset);

$("#spot-input").value = "103";
renderLegs();
loadPresets();
analyze();
