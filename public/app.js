/* ArbScanner Frontend */

const REFRESH_INTERVAL = 120; // seconds between auto-refreshes
const DISPLAY_LIMIT = 200;

let allOpportunities = [];
let stats = {};
let countdownVal = REFRESH_INTERVAL;
let countdownTimer = null;
let refreshTimer = null;
let isLoading = false;

const $ = id => document.getElementById(id);

// ── Fetch Data ───────────────────────────────────────────────────────
async function fetchData() {
  if (isLoading) return;
  isLoading = true;
  setRefreshSpinner(true);

  try {
    const res = await fetch('/api/arb');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    allOpportunities = data.opportunities || [];
    stats = data.stats || {};

    updateStats(stats);
    renderTable();
    updateAlertBanner(stats);
    updateLastUpdated(data.lastUpdated);
    showKalshiSetup(stats);
  } catch (err) {
    showAlert('error', `Failed to load data: ${err.message}. Retrying…`);
    console.error(err);
  } finally {
    isLoading = false;
    setRefreshSpinner(false);
    resetCountdown();
  }
}

// ── Stats Bar ─────────────────────────────────────────────────────────
function updateStats(s) {
  $('sTotal').textContent = s.totalOpportunities ?? '—';
  $('sExec').textContent = s.executableOpportunities ?? '—';
  $('sBest').textContent = s.bestProfit != null ? `${s.bestProfit.toFixed(2)}%` : '—';
  $('sAvg').textContent  = s.avgProfit  != null ? `${s.avgProfit.toFixed(2)}%`  : '—';
  $('sPoly').textContent    = s.polymarketMarkets ?? '—';
  $('sKalshi').textContent  = s.kalshiMarkets ?? '—';
  $('sMatched').textContent = s.matchedMarkets ?? '—';

  // Color best profit
  if (s.bestProfit != null) {
    $('sBest').className = 'stat-val ' + profitClass(s.bestProfit);
    $('sAvg').className  = 'stat-val ' + profitClass(s.avgProfit);
  }
}

function updateAlertBanner(s) {
  const banner = $('alertBanner');
  const msgs = [];

  if (s.polymarketStatus && s.polymarketStatus !== 'ok') {
    msgs.push(`⚠ Polymarket: ${s.polymarketStatus}`);
  }
  if (s.kalshiStatus === 'not_configured') {
    // handled by setup card
  } else if (s.kalshiStatus && s.kalshiStatus !== 'ok') {
    msgs.push(`⚠ Kalshi: ${s.kalshiStatus}`);
  }

  if (msgs.length > 0) {
    banner.className = 'alert-banner warn';
    banner.textContent = msgs.join(' · ');
  } else {
    banner.className = 'alert-banner hidden';
  }
}

function showKalshiSetup(s) {
  // Public API — no credentials needed, always hide setup card
  $('kalshiSetupCard').classList.add('hidden');
}

function updateLastUpdated(iso) {
  if (!iso) return;
  const d = new Date(iso);
  $('lastUpdated').textContent = `Updated ${d.toLocaleTimeString()}`;
}

// ── Table Rendering ───────────────────────────────────────────────────
function getFiltered() {
  const search    = $('searchInput').value.trim().toLowerCase();
  const minProfit = parseFloat($('minProfitFilter').value) || 0;
  const typeVal   = $('typeFilter').value;
  const sortVal   = $('sortBy').value;

  let filtered = allOpportunities.filter(o => {
    if (o.profitPct < minProfit) return false;
    if (typeVal === 'executable' &&  o.isMidPriceOnly) return false;
    if (typeVal === 'indicative' && !o.isMidPriceOnly) return false;
    if (search) {
      const text = (o.polyQuestion + ' ' + o.kalshiQuestion).toLowerCase();
      if (!text.includes(search)) return false;
    }
    return true;
  });

  filtered.sort((a, b) => {
    if (sortVal === 'profit')  return b.profitPct - a.profitPct;
    if (sortVal === 'volume')  return b.polyVolume - a.polyVolume;
    if (sortVal === 'match')   return b.matchScore - a.matchScore;
    return b.profitPct - a.profitPct;
  });

  return filtered;
}

function renderTable() {
  const filtered = getFiltered();
  const table = $('arbTable');
  const body  = $('arbBody');
  const loadingState = $('loadingState');
  const emptyState   = $('emptyState');

  loadingState.classList.add('hidden');

  $('resultCount').innerHTML = `Showing <strong>${Math.min(filtered.length, DISPLAY_LIMIT)}</strong> of <strong>${allOpportunities.length}</strong> opportunities`;

  if (filtered.length === 0) {
    table.classList.add('hidden');
    emptyState.classList.remove('hidden');
    const sub = $('emptySubText');
    if (allOpportunities.length === 0 && stats.kalshiStatus === 'not_configured') {
      sub.textContent = 'Configure Kalshi credentials below to enable scanning.';
    } else if (allOpportunities.length === 0) {
      sub.textContent = 'No matching markets found between Polymarket and Kalshi.';
    } else {
      sub.textContent = 'Try lowering the minimum profit filter.';
    }
    return;
  }

  emptyState.classList.add('hidden');
  table.classList.remove('hidden');

  const rows = filtered.slice(0, DISPLAY_LIMIT).map(buildRow).join('');
  body.innerHTML = rows;
}

function buildRow(o) {
  const pctClass  = profitClass(o.profitPct);
  const rowClass  = rowProfitClass(o.profitPct) + (o.isMidPriceOnly ? ' row-indicative' : '');
  const pctDisplay = `${o.profitPct.toFixed(2)}%`;
  const absDisplay = `${(o.profit * 100).toFixed(1)}¢ per $1`;

  const yesPlatformClass  = o.buyYesPlatform.toLowerCase();
  const noPlatformClass   = o.buyNoPlatform.toLowerCase();

  const polyVol = formatVolume(o.polyVolume);
  const kVol    = formatVolume(o.kalshiVolume);

  const categoryTag = o.kalshiCategory
    ? `<span class="category-tag">${escHtml(o.kalshiCategory)}</span>`
    : '';
  const indicativeBadge = o.isMidPriceOnly
    ? '<span class="indicative-badge">INDICATIVE</span>'
    : '';
  const matchStr = (o.matchScore * 100).toFixed(0);

  return `
<tr class="${rowClass}">
  <td class="event-cell col-event">
    <div class="event-name" title="${escHtml(o.polyQuestion)}">${escHtml(truncate(o.polyQuestion, 120))}</div>
    <div class="event-meta">
      ${categoryTag}
      ${indicativeBadge}
      <span class="match-score">${matchStr}% match</span>
    </div>
  </td>
  <td class="strategy-cell col-strategy">
    <div class="strategy-row">
      <span class="s-label">YES →</span>
      <span class="s-platform ${yesPlatformClass}">${o.buyYesPlatform}</span>
    </div>
    <div class="strategy-row">
      <span class="s-label">NO →</span>
      <span class="s-platform ${noPlatformClass}">${o.buyNoPlatform}</span>
    </div>
  </td>
  <td class="col-price">
    <div class="price-val mono">${(o.yesPricePaid * 100).toFixed(1)}¢</div>
    <div class="price-sub">${o.buyYesPlatform}</div>
  </td>
  <td class="col-price">
    <div class="price-val mono">${(o.noPricePaid * 100).toFixed(1)}¢</div>
    <div class="price-sub">${o.buyNoPlatform}</div>
  </td>
  <td class="col-cost">
    <div class="cost-val mono">${(o.totalCost * 100).toFixed(1)}¢</div>
  </td>
  <td class="profit-cell col-profit">
    <div class="profit-pct ${pctClass}">${pctDisplay}</div>
    <div class="profit-abs">${absDisplay}</div>
  </td>
  <td class="col-volume">
    <div class="vol-val">$${polyVol}</div>
    <div class="vol-sub">Poly vol</div>
  </td>
  <td class="links-cell col-links">
    <a href="${escHtml(o.polyUrl)}" target="_blank" rel="noopener" class="link-btn poly">PM</a>
    <a href="${escHtml(o.kalshiUrl)}" target="_blank" rel="noopener" class="link-btn kalshi">KL</a>
  </td>
</tr>`;
}

// ── Helpers ───────────────────────────────────────────────────────────
function profitClass(pct) {
  if (pct >= 3) return 'profit-high';
  if (pct >= 1) return 'profit-med';
  return 'profit-low';
}

function rowProfitClass(pct) {
  if (pct >= 3) return 'row-hi';
  if (pct >= 1) return 'row-med';
  return 'row-lo';
}

function formatVolume(n) {
  if (n == null || isNaN(n)) return '—';
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
  if (n >= 1_000)     return (n / 1_000).toFixed(0) + 'K';
  return n.toFixed(0);
}

function truncate(str, len) {
  return str.length > len ? str.slice(0, len - 1) + '…' : str;
}

function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function showAlert(type, msg) {
  const b = $('alertBanner');
  b.className = `alert-banner ${type}`;
  b.textContent = msg;
}

function setRefreshSpinner(on) {
  const icon = $('spinIcon');
  if (on) icon.classList.add('spinning');
  else    icon.classList.remove('spinning');
}

// ── Countdown Timer ──────────────────────────────────────────────────
function resetCountdown() {
  countdownVal = REFRESH_INTERVAL;
  clearInterval(countdownTimer);
  countdownTimer = setInterval(() => {
    countdownVal--;
    $('countdown').textContent = countdownVal + 's';
    if (countdownVal <= 0) {
      clearInterval(countdownTimer);
      fetchData();
    }
  }, 1000);
  $('countdown').textContent = countdownVal + 's';
}

// ── Event Listeners ──────────────────────────────────────────────────
$('refreshBtn').addEventListener('click', () => {
  clearInterval(countdownTimer);
  fetchData();
});

$('searchInput').addEventListener('input', () => {
  const val = $('searchInput').value;
  $('searchClear').classList.toggle('hidden', !val);
  renderTable();
});

$('searchClear').addEventListener('click', () => {
  $('searchInput').value = '';
  $('searchClear').classList.add('hidden');
  renderTable();
});

$('minProfitFilter').addEventListener('change', renderTable);
$('typeFilter').addEventListener('change', renderTable);
$('sortBy').addEventListener('change', renderTable);

// ── Init ──────────────────────────────────────────────────────────────
fetchData();
