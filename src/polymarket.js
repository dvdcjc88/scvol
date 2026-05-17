const POLYMARKET_API = 'https://gamma-api.polymarket.com/markets';
const MIN_LIQUIDITY = 500;

export async function fetchPolymarketMarkets() {
  const params = new URLSearchParams({
    active: 'true',
    closed: 'false',
    limit: '500',
    order: 'volume',
    ascending: 'false'
  });

  const res = await fetch(`${POLYMARKET_API}?${params}`, {
    headers: { 'Accept': 'application/json' },
    signal: AbortSignal.timeout(15000)
  });

  if (!res.ok) throw new Error(`Polymarket API error: ${res.status}`);

  const raw = await res.json();
  const markets = Array.isArray(raw) ? raw : [];

  return markets
    .filter(m => {
      const outcomes = parseJSON(m.outcomes);
      const prices = parseJSON(m.outcomePrices);
      return (
        outcomes && prices &&
        outcomes.length === 2 &&
        prices.length === 2 &&
        parseFloat(m.liquidity || 0) >= MIN_LIQUIDITY
      );
    })
    .map(m => {
      const outcomes = parseJSON(m.outcomes);
      const prices = parseJSON(m.outcomePrices);
      const yesPrice = parseFloat(prices[0]);
      const noPrice = parseFloat(prices[1]);
      const bestAsk = parseFloat(m.bestAsk) || yesPrice;
      const bestBid = parseFloat(m.bestBid) || yesPrice;

      return {
        id: m.id,
        platform: 'polymarket',
        question: m.question || '',
        slug: m.slug || '',
        yesPrice,
        noPrice,
        bestAsk,
        bestBid,
        yesMid: (bestAsk + bestBid) / 2,
        noMid: 1 - (bestAsk + bestBid) / 2,
        volume: parseFloat(m.volume) || 0,
        volume24hr: parseFloat(m.volume24hr) || 0,
        liquidity: parseFloat(m.liquidity) || 0,
        url: `https://polymarket.com/event/${m.slug}`,
        endDate: m.endDate,
        outcomes,
        spread: bestAsk - bestBid
      };
    })
    .filter(m => m.yesPrice > 0.01 && m.yesPrice < 0.99);
}

function parseJSON(val) {
  if (Array.isArray(val)) return val;
  if (typeof val === 'string') {
    try { return JSON.parse(val); } catch { return null; }
  }
  return null;
}
