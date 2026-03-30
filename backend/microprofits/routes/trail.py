from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

router = APIRouter(prefix="/api/trail", tags=["trail"])


@router.get("/states")
async def get_trail_states(request: Request):
    """Live in-memory trail state for all tracked positions."""
    loop = request.app.state.loop
    states = loop._pct_trailer.trail_states  # dict[deal_id, TrailState]

    result = []
    for deal_id, st in states.items():
        result.append({
            "deal_id": deal_id,
            "peak_upl": round(st.peak_upl, 2),
            "trail_level": round(st.trail_level, 2),
            "initial_sl": round(st.initial_sl, 2),
            "activated": st.activated,
        })
    return result


@router.get("/history")
async def get_trail_history(
    request: Request,
    deal_id: str | None = Query(None),
    epic: str | None = Query(None),
    limit: int = Query(500, ge=1, le=5000),
):
    """Trail snapshots from DB for post-trade analysis."""
    store = request.app.state.store
    rows = await store.get_trail_history(deal_id=deal_id, epic=epic, limit=limit)
    for r in rows:
        if "ts" in r and r["ts"]:
            r["ts"] = r["ts"].isoformat()
    return rows


TRAIL_VIEWER_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Trail Monitor</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f1117; color: #e1e4e8; padding: 20px; }
  h1 { font-size: 1.4rem; margin-bottom: 16px; color: #58a6ff; }
  h2 { font-size: 1.1rem; margin: 24px 0 12px; color: #8b949e; }

  .filters { display: flex; gap: 12px; flex-wrap: wrap; align-items: end; margin-bottom: 20px; background: #161b22; padding: 16px; border-radius: 8px; border: 1px solid #30363d; }
  .filter-group { display: flex; flex-direction: column; gap: 4px; }
  .filter-group label { font-size: 0.75rem; color: #8b949e; text-transform: uppercase; letter-spacing: 0.5px; }
  .filter-group input, .filter-group select { background: #0d1117; border: 1px solid #30363d; color: #e1e4e8; padding: 6px 10px; border-radius: 4px; font-size: 0.85rem; }
  .filter-group input:focus, .filter-group select:focus { outline: none; border-color: #58a6ff; }
  button { background: #238636; color: #fff; border: none; padding: 8px 16px; border-radius: 4px; cursor: pointer; font-size: 0.85rem; font-weight: 500; }
  button:hover { background: #2ea043; }
  button.secondary { background: #30363d; }
  button.secondary:hover { background: #3d444d; }

  /* Live states */
  .live-cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 12px; margin-bottom: 24px; }
  .live-card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 14px; }
  .live-card .deal { font-size: 0.75rem; color: #8b949e; margin-bottom: 8px; word-break: break-all; }
  .live-card .metrics { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
  .metric { display: flex; flex-direction: column; }
  .metric .label { font-size: 0.7rem; color: #8b949e; text-transform: uppercase; }
  .metric .value { font-size: 1.1rem; font-weight: 600; }
  .metric .value.positive { color: #3fb950; }
  .metric .value.negative { color: #f85149; }
  .metric .value.neutral { color: #d29922; }
  .badge { display: inline-block; font-size: 0.65rem; padding: 2px 6px; border-radius: 3px; font-weight: 600; text-transform: uppercase; }
  .badge.active { background: #238636; color: #fff; }
  .badge.inactive { background: #30363d; color: #8b949e; }
  .no-data { color: #8b949e; font-style: italic; padding: 20px; text-align: center; }

  /* History table */
  .table-wrap { overflow-x: auto; border-radius: 8px; border: 1px solid #30363d; }
  table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
  th { background: #161b22; color: #8b949e; text-transform: uppercase; font-size: 0.7rem; letter-spacing: 0.5px; padding: 10px 12px; text-align: left; position: sticky; top: 0; }
  td { padding: 8px 12px; border-top: 1px solid #21262d; }
  tr:hover td { background: #161b22; }
  .event-badge { font-size: 0.7rem; padding: 2px 8px; border-radius: 3px; font-weight: 600; }
  .event-INIT { background: #1f6feb33; color: #58a6ff; }
  .event-TRAIL_MOVE { background: #23863633; color: #3fb950; }
  .event-EXIT { background: #f8514933; color: #f85149; }
  .event-TICK { background: #30363d; color: #8b949e; }

  /* Pagination */
  .pagination { display: flex; justify-content: space-between; align-items: center; margin-top: 12px; padding: 8px 0; }
  .pagination .info { font-size: 0.8rem; color: #8b949e; }
  .pagination .controls { display: flex; gap: 8px; }

  /* Auto-refresh indicator */
  .refresh-bar { display: flex; align-items: center; gap: 8px; margin-bottom: 16px; }
  .refresh-dot { width: 8px; height: 8px; border-radius: 50%; background: #3fb950; animation: pulse 2s infinite; }
  .refresh-dot.off { background: #484f58; animation: none; }
  @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }
  .refresh-label { font-size: 0.75rem; color: #8b949e; }
</style>
</head>
<body>

<h1>Trail Monitor</h1>

<!-- Live States -->
<div class="refresh-bar">
  <div class="refresh-dot" id="refreshDot"></div>
  <span class="refresh-label" id="refreshLabel">Auto-refresh: 3s</span>
  <button class="secondary" onclick="toggleAutoRefresh()" id="toggleBtn" style="padding:4px 10px; font-size:0.75rem;">Pause</button>
</div>

<h2>Live Trail States</h2>
<div id="liveStates" class="live-cards"><div class="no-data">Loading...</div></div>

<!-- History Filters -->
<h2>Trail History</h2>
<div class="filters">
  <div class="filter-group">
    <label>Symbol</label>
    <select id="fEpic">
      <option value="">All</option>
      <option value="US100">US100</option>
      <option value="GOLD">GOLD</option>
    </select>
  </div>
  <div class="filter-group">
    <label>Trade</label>
    <select id="fDealId" style="min-width:180px;">
      <option value="">All trades</option>
    </select>
  </div>
  <div class="filter-group">
    <label>Event</label>
    <select id="fEvent">
      <option value="">All</option>
      <option value="INIT">INIT</option>
      <option value="TRAIL_MOVE">TRAIL_MOVE</option>
      <option value="EXIT">EXIT</option>
    </select>
  </div>
  <div class="filter-group">
    <label>From</label>
    <input type="date" id="fFrom">
  </div>
  <div class="filter-group">
    <label>To</label>
    <input type="date" id="fTo">
  </div>
  <div class="filter-group">
    <label>&nbsp;</label>
    <button onclick="loadHistory(1)">Search</button>
  </div>
</div>

<div class="table-wrap">
  <table>
    <thead>
      <tr>
        <th>Time</th>
        <th>Symbol</th>
        <th>Deal ID</th>
        <th>Event</th>
        <th>UPL</th>
        <th>Peak UPL</th>
        <th>Trail Level</th>
        <th>Initial SL</th>
        <th>Trail %</th>
        <th>Activated</th>
      </tr>
    </thead>
    <tbody id="historyBody">
      <tr><td colspan="10" class="no-data">Loading...</td></tr>
    </tbody>
  </table>
</div>

<div class="pagination">
  <span class="info" id="pageInfo">-</span>
  <div class="controls">
    <button class="secondary" id="prevBtn" onclick="loadHistory(currentPage - 1)" disabled>Prev</button>
    <button class="secondary" id="nextBtn" onclick="loadHistory(currentPage + 1)" disabled>Next</button>
  </div>
</div>

<script>
const API = window.location.origin;
const PAGE_SIZE = 50;
let currentPage = 1;
let autoRefresh = true;
let refreshTimer = null;

// Friendly name map: deal_id -> "US100-01", "GOLD-03", etc.
let dealNames = {};    // deal_id -> friendly name
let dealIdLookup = {}; // friendly name -> deal_id

function buildDealNames(data) {
  // Find INIT events sorted by time (oldest first) to assign sequential numbers
  const inits = data.filter(r => r.event === 'INIT').reverse();
  const counters = {};  // epic -> count

  // First pass: assign names from INIT events
  for (const r of inits) {
    if (dealNames[r.deal_id]) continue;
    counters[r.epic] = (counters[r.epic] || 0) + 1;
    const name = `${r.epic}-${String(counters[r.epic]).padStart(2, '0')}`;
    dealNames[r.deal_id] = name;
    dealIdLookup[name] = r.deal_id;
  }

  // Second pass: catch any deal_ids without INIT (e.g., if data is partial)
  for (const r of data) {
    if (dealNames[r.deal_id]) continue;
    counters[r.epic] = (counters[r.epic] || 0) + 1;
    const name = `${r.epic}-${String(counters[r.epic]).padStart(2, '0')}`;
    dealNames[r.deal_id] = name;
    dealIdLookup[name] = r.deal_id;
  }
}

function friendlyName(dealId) {
  return dealNames[dealId] || dealId.slice(0, 8);
}

function populateDealPicker() {
  const select = document.getElementById('fDealId');
  const current = select.value;

  // Build options from dealNames (already deduplicated by buildDealNames)
  const entries = Object.entries(dealNames)
    .map(([dealId, name]) => {
      // Find the earliest timestamp for this deal
      const rows = allData.filter(r => r.deal_id === dealId);
      const earliest = rows.length ? rows[rows.length - 1] : null;
      const date = earliest ? earliest.ts.slice(0, 10) : '';
      return { dealId, name, date };
    })
    .sort((a, b) => a.name.localeCompare(b.name));

  select.innerHTML = '<option value="">All trades</option>';
  for (const e of entries) {
    const opt = document.createElement('option');
    opt.value = e.dealId;
    opt.textContent = `${e.name}  (${e.date})`;
    select.appendChild(opt);
  }
  select.value = current;
}

// -- Live states --
async function loadLiveStates() {
  try {
    const res = await fetch(`${API}/api/trail/states`);
    const data = await res.json();
    const el = document.getElementById('liveStates');

    if (!data.length) {
      el.innerHTML = '<div class="no-data">No positions being trailed right now</div>';
      return;
    }

    el.innerHTML = data.map(s => `
      <div class="live-card">
        <div class="deal"><strong>${friendlyName(s.deal_id)}</strong> <span style="color:#484f58;font-size:0.65rem;">${s.deal_id}</span></div>
        <div class="metrics">
          <div class="metric">
            <span class="label">Peak UPL</span>
            <span class="value ${s.peak_upl > 0 ? 'positive' : s.peak_upl < 0 ? 'negative' : ''}">\$${s.peak_upl.toFixed(2)}</span>
          </div>
          <div class="metric">
            <span class="label">Trail Level</span>
            <span class="value neutral">\$${s.trail_level.toFixed(2)}</span>
          </div>
          <div class="metric">
            <span class="label">Initial SL</span>
            <span class="value">\$${s.initial_sl.toFixed(2)}</span>
          </div>
          <div class="metric">
            <span class="label">Status</span>
            <span class="badge ${s.activated ? 'active' : 'inactive'}">${s.activated ? 'Active' : 'Waiting'}</span>
          </div>
        </div>
      </div>
    `).join('');
  } catch (e) {
    console.error('Failed to load live states:', e);
  }
}

// -- History --
let allData = [];  // cached for filtering

async function fetchAllData() {
  const res = await fetch(`${API}/api/trail/history?limit=5000`);
  allData = await res.json();
  buildDealNames(allData);
  populateDealPicker();
}

async function loadHistory(page) {
  if (page < 1) return;
  currentPage = page;

  if (!allData.length) await fetchAllData();

  const epic = document.getElementById('fEpic').value;
  const dealId = document.getElementById('fDealId').value;
  const eventFilter = document.getElementById('fEvent').value;
  const from = document.getElementById('fFrom').value;
  const to = document.getElementById('fTo').value;

  let data = allData;
  if (epic) data = data.filter(r => r.epic === epic);
  if (dealId) data = data.filter(r => r.deal_id === dealId);
  if (eventFilter) data = data.filter(r => r.event === eventFilter);
  if (from) data = data.filter(r => r.ts && r.ts.slice(0, 10) >= from);
  if (to) data = data.filter(r => r.ts && r.ts.slice(0, 10) <= to);

  const total = data.length;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  if (currentPage > totalPages) currentPage = totalPages;

  const start = (currentPage - 1) * PAGE_SIZE;
  const pageData = data.slice(start, start + PAGE_SIZE);

  const tbody = document.getElementById('historyBody');
  if (!pageData.length) {
    tbody.innerHTML = '<tr><td colspan="10" class="no-data">No trail snapshots found</td></tr>';
  } else {
    tbody.innerHTML = pageData.map(r => {
      const ts = r.ts ? formatTs(r.ts) : '-';
      const uplClass = r.upl > 0 ? 'positive' : r.upl < 0 ? 'negative' : '';
      const peakClass = r.peak_upl > 0 ? 'positive' : '';
      const name = friendlyName(r.deal_id);
      return `<tr>
        <td>${ts}</td>
        <td>${r.epic}</td>
        <td title="${r.deal_id}" style="cursor:pointer;" onclick="filterByDeal('${r.deal_id}')">${name}</td>
        <td><span class="event-badge event-${r.event}">${r.event}</span></td>
        <td class="${uplClass}">\$${r.upl.toFixed(2)}</td>
        <td class="${peakClass}">\$${r.peak_upl.toFixed(2)}</td>
        <td>\$${r.trail_level.toFixed(2)}</td>
        <td>\$${r.initial_sl.toFixed(2)}</td>
        <td>${r.trail_pct}%</td>
        <td>${r.activated ? 'Yes' : 'No'}</td>
      </tr>`;
    }).join('');
  }

  document.getElementById('pageInfo').textContent =
    `Showing ${start + 1}-${Math.min(start + PAGE_SIZE, total)} of ${total} snapshots (page ${currentPage}/${totalPages})`;
  document.getElementById('prevBtn').disabled = currentPage <= 1;
  document.getElementById('nextBtn').disabled = currentPage >= totalPages;
}

function filterByDeal(dealId) {
  document.getElementById('fDealId').value = dealId;
  loadHistory(1);
}

function formatTs(iso) {
  const d = new Date(iso);
  const pad = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function toggleAutoRefresh() {
  autoRefresh = !autoRefresh;
  const dot = document.getElementById('refreshDot');
  const label = document.getElementById('refreshLabel');
  const btn = document.getElementById('toggleBtn');
  if (autoRefresh) {
    dot.classList.remove('off');
    label.textContent = 'Auto-refresh: 3s';
    btn.textContent = 'Pause';
    startAutoRefresh();
  } else {
    dot.classList.add('off');
    label.textContent = 'Auto-refresh: paused';
    btn.textContent = 'Resume';
    if (refreshTimer) clearInterval(refreshTimer);
  }
}

function startAutoRefresh() {
  if (refreshTimer) clearInterval(refreshTimer);
  refreshTimer = setInterval(() => {
    if (autoRefresh) loadLiveStates();
  }, 3000);
}

// Refresh data cache periodically
async function refreshData() {
  await fetchAllData();
  loadHistory(currentPage);
}

// Init
fetchAllData().then(() => {
  loadLiveStates();
  loadHistory(1);
});
startAutoRefresh();
setInterval(refreshData, 30000); // refresh history data every 30s
</script>
</body>
</html>
"""


@router.get("/viewer", response_class=HTMLResponse)
async def trail_viewer():
    """Human-readable trail monitor page."""
    return TRAIL_VIEWER_HTML
