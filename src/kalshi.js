const KALSHI_BASE = 'https://external-api.kalshi.com/trade-api/v2';

export async function fetchKalshiMarkets() {
  let allMarkets = [];
  let cursor = null;

  do {
    const params = new URLSearchParams({
      limit: '200',
      status: 'open',
      with_nested_markets: 'true'
    });
    if (cursor) params.set('cursor', cursor);

    const res = await fetch(`${KALSHI_BASE}/events?${params}`, {
      headers: { 'Accept': 'application/json' },
      signal: AbortSignal.timeout(20000)
    });

    if (!res.ok) throw new Error(`Kalshi API error: ${res.status}`);

    const data = await res.json();
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

        // Use the market-level title; fall back to event title
        const question = m.title || event.title || '';
        if (!question) continue;

        // Build a clean URL: derive series slug from event_ticker
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
          volume:       parseFloat(m.volume_fp)    || 0,
          volume24h:    parseFloat(m.volume_24h_fp) || 0,
          liquidity:    parseFloat(m.liquidity_dollars) || 0,
          url,
          closeTime: m.close_time
        });
      }
    }

    cursor = data.cursor && events.length === 200 ? data.cursor : null;
    if (allMarkets.length >= 3000) break;
  } while (cursor);

  // Filter: meaningful price spread, not trivially one-sided
  return allMarkets.filter(m =>
    m.yesMid > 0.01 && m.yesMid < 0.99 &&
    m.noMid  > 0.01 && m.noMid  < 0.99
  );
}
