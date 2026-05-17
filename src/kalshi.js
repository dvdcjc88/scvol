const PUBLIC_BASE  = 'https://external-api.kalshi.com/trade-api/v2';
const TRADING_BASE = 'https://trading-api.kalshi.com/trade-api/v2';

let cachedToken = null;
let tokenExpiry = 0;

async function getAuthToken() {
  const email    = process.env.KALSHI_EMAIL;
  const password = process.env.KALSHI_PASSWORD;
  if (!email || !password) return null;

  if (cachedToken && Date.now() < tokenExpiry) return cachedToken;

  const res = await fetch(`${TRADING_BASE}/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
    body: JSON.stringify({ email, password }),
    signal: AbortSignal.timeout(10000)
  });

  if (!res.ok) {
    console.warn(`Kalshi auth failed (${res.status}), falling back to public API`);
    return null;
  }

  const data = await res.json();
  cachedToken = data.token;
  tokenExpiry = Date.now() + 23 * 60 * 60 * 1000; // re-auth after 23 h
  console.log('Kalshi: authenticated via email/password');
  return cachedToken;
}

export async function fetchKalshiMarkets() {
  const token   = await getAuthToken();
  const BASE    = token ? TRADING_BASE : PUBLIC_BASE;
  const headers = { 'Accept': 'application/json' };
  if (token) headers['Authorization'] = `Bearer ${token}`;

  let allMarkets = [];
  let cursor = null;

  do {
    const params = new URLSearchParams({
      limit: '200',
      status: 'open',
      with_nested_markets: 'true'
    });
    if (cursor) params.set('cursor', cursor);

    const res = await fetch(`${BASE}/events?${params}`, {
      headers,
      signal: AbortSignal.timeout(20000)
    });

    if (!res.ok) throw new Error(`Kalshi API error: ${res.status}`);

    const data   = await res.json();
    const events = data.events || [];

    for (const event of events) {
      const markets = event.markets || [];
      for (const m of markets) {
        if (m.status !== 'active' && m.status !== 'open') continue;
        if (m.market_type !== 'binary') continue;

        const yesAsk = parseFloat(m.yes_ask_dollars);
        const yesBid = parseFloat(m.yes_bid_dollars);
        const noAsk  = parseFloat(m.no_ask_dollars);
        const noBid  = parseFloat(m.no_bid_dollars);

        if (isNaN(yesAsk) || isNaN(noAsk)) continue;

        const question = m.title || event.title || '';
        if (!question) continue;

        const eventSlug = (m.event_ticker || event.event_ticker || '').toLowerCase();
        const url = `https://kalshi.com/markets/${eventSlug}`;

        allMarkets.push({
          id: m.ticker,
          platform: 'kalshi',
          question,
          eventQuestion: event.title || '',
          ticker: m.ticker,
          category: event.category || '',
          yesAsk,
          yesBid,
          noAsk,
          noBid,
          yesMid: (yesAsk + yesBid) / 2,
          noMid:  (noAsk  + noBid)  / 2,
          volume:    parseFloat(m.volume_fp)        || 0,
          volume24h: parseFloat(m.volume_24h_fp)    || 0,
          liquidity: parseFloat(m.liquidity_dollars) || 0,
          url,
          closeTime: m.close_time
        });
      }
    }

    cursor = data.cursor && events.length === 200 ? data.cursor : null;
    if (allMarkets.length >= 5000) break;
  } while (cursor);

  return allMarkets.filter(m =>
    m.yesMid > 0.01 && m.yesMid < 0.99 &&
    m.noMid  > 0.01 && m.noMid  < 0.99
  );
}
