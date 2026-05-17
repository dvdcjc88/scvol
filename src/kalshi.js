const KALSHI_BASE = 'https://trading-api.kalshi.com/trade-api/v2';

let sessionToken = null;
let tokenExpiry = 0;

async function getAuthHeaders() {
  if (process.env.KALSHI_API_KEY) {
    return { 'Authorization': process.env.KALSHI_API_KEY };
  }
  if (process.env.KALSHI_EMAIL && process.env.KALSHI_PASSWORD) {
    const now = Date.now();
    if (!sessionToken || now > tokenExpiry) {
      sessionToken = await login();
      tokenExpiry = now + 20 * 60 * 1000; // re-login every 20 min
    }
    return { 'Authorization': sessionToken };
  }
  return null; // No auth configured
}

async function login() {
  const res = await fetch(`${KALSHI_BASE}/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      email: process.env.KALSHI_EMAIL,
      password: process.env.KALSHI_PASSWORD
    }),
    signal: AbortSignal.timeout(10000)
  });
  if (!res.ok) throw new Error(`Kalshi login failed: ${res.status}`);
  const data = await res.json();
  return data.token;
}

export async function fetchKalshiMarkets() {
  const authHeaders = await getAuthHeaders();
  if (!authHeaders) {
    throw new Error('NO_AUTH: Set KALSHI_API_KEY or KALSHI_EMAIL+KALSHI_PASSWORD env vars');
  }

  const headers = {
    'Content-Type': 'application/json',
    ...authHeaders
  };

  let allMarkets = [];
  let cursor = null;

  do {
    const params = new URLSearchParams({ limit: '200', status: 'open' });
    if (cursor) params.set('cursor', cursor);

    const res = await fetch(`${KALSHI_BASE}/markets?${params}`, {
      headers,
      signal: AbortSignal.timeout(15000)
    });

    if (res.status === 401) {
      sessionToken = null; // force re-login next time
      throw new Error('Kalshi authentication failed. Check your credentials.');
    }
    if (!res.ok) throw new Error(`Kalshi API error: ${res.status}`);

    const data = await res.json();
    const batch = data.markets || [];
    allMarkets = allMarkets.concat(batch);
    cursor = data.cursor && batch.length === 200 ? data.cursor : null;

    if (allMarkets.length >= 2000) break;
  } while (cursor);

  return allMarkets
    .filter(m => m.status === 'open' && m.yes_ask != null && m.no_ask != null)
    .map(m => ({
      id: m.ticker,
      platform: 'kalshi',
      question: m.title || m.subtitle || m.ticker,
      ticker: m.ticker,
      category: m.category || '',
      yesAsk: m.yes_ask / 100,
      yesBid: m.yes_bid / 100,
      noAsk: m.no_ask / 100,
      noBid: m.no_bid / 100,
      yesMid: (m.yes_ask + m.yes_bid) / 2 / 100,
      noMid: (m.no_ask + m.no_bid) / 2 / 100,
      volume: m.volume || 0,
      openInterest: m.open_interest || 0,
      url: `https://kalshi.com/markets/${m.ticker.split('-').slice(0, -1).join('-').toLowerCase() || m.ticker.toLowerCase()}`,
      closeTime: m.close_time
    }))
    .filter(m => m.yesMid > 0.01 && m.yesMid < 0.99);
}
