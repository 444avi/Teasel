const $ = (selector) => document.querySelector(selector);

async function apiJson(response) {
  const result = await response.json();
  if (response.status === 401) {
    window.location.assign(result.auth_url || "/");
    throw new Error("Your session needs renewal. Redirecting to sign in…");
  }
  if (response.status === 403) throw new Error("Your account does not have access to this tool.");
  if (response.status === 429) {
    const seconds = Number(response.headers.get("Retry-After")) || 60;
    throw new Error(`Request limit reached. Try again in ${seconds} seconds.`);
  }
  if (response.status === 503) throw new Error("Session verification is temporarily unavailable. Please retry shortly.");
  if (!response.ok) throw new Error(result.error || "The request failed.");
  return result;
}

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
