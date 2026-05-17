import { fetchPolymarketMarkets } from './polymarket.js';
import { fetchKalshiMarkets } from './kalshi.js';
import { findMatches } from './matcher.js';

const FEE_BUFFER = 0.02; // 2% buffer to account for fees/slippage

function calcArb(poly, kalshi) {
  const opps = [];

  // Use ask prices (actual cost to buy)
  const polyYesAsk = poly.bestAsk || poly.yesPrice;
  const polyNoAsk = poly.noPrice; // AMM: noPrice ≈ 1 - yesPrice (includes spread)
  const kalshiYesAsk = kalshi.yesAsk;
  const kalshiNoAsk = kalshi.noAsk;

  // Also compute using mid prices for comparison
  const polyYesMid = poly.yesMid || poly.yesPrice;
  const polyNoMid = poly.noMid || poly.noPrice;
  const kalshiYesMid = kalshi.yesMid;
  const kalshiNoMid = kalshi.noMid;

  // Strategy A: Buy YES on Polymarket + NO on Kalshi
  const costA = polyYesAsk + kalshiNoAsk;
  if (costA < 1 - FEE_BUFFER) {
    const profit = 1 - costA;
    opps.push({
      strategy: 'Buy YES Poly + NO Kalshi',
      buyYesPlatform: 'Polymarket',
      buyNoPlatform: 'Kalshi',
      yesPricePaid: polyYesAsk,
      noPricePaid: kalshiNoAsk,
      totalCost: costA,
      profit,
      profitPct: profit * 100,
      // Mid-price profit for display
      midProfit: (1 - polyYesMid - kalshiNoMid) * 100
    });
  }

  // Strategy B: Buy YES on Kalshi + NO on Polymarket
  const costB = kalshiYesAsk + polyNoAsk;
  if (costB < 1 - FEE_BUFFER) {
    const profit = 1 - costB;
    opps.push({
      strategy: 'Buy YES Kalshi + NO Poly',
      buyYesPlatform: 'Kalshi',
      buyNoPlatform: 'Polymarket',
      yesPricePaid: kalshiYesAsk,
      noPricePaid: polyNoAsk,
      totalCost: costB,
      profit,
      profitPct: profit * 100,
      midProfit: (1 - kalshiYesMid - polyNoMid) * 100
    });
  }

  // Also check mid-price arb (more lenient — useful for showing near-arb)
  if (opps.length === 0) {
    const midCostA = polyYesMid + kalshiNoMid;
    const midCostB = kalshiYesMid + polyNoMid;
    const bestMid = Math.min(midCostA, midCostB);
    if (bestMid < 1) {
      const isCostA = midCostA <= midCostB;
      opps.push({
        strategy: isCostA ? 'Buy YES Poly + NO Kalshi' : 'Buy YES Kalshi + NO Poly',
        buyYesPlatform: isCostA ? 'Polymarket' : 'Kalshi',
        buyNoPlatform: isCostA ? 'Kalshi' : 'Polymarket',
        yesPricePaid: isCostA ? polyYesMid : kalshiYesMid,
        noPricePaid: isCostA ? kalshiNoMid : polyNoMid,
        totalCost: bestMid,
        profit: 1 - bestMid,
        profitPct: (1 - bestMid) * 100,
        midProfit: (1 - bestMid) * 100,
        isMidPriceOnly: true // indicative, not executable at these prices
      });
    }
  }

  return opps;
}

export async function fetchArbitrageOpportunities() {
  const stats = {
    totalOpportunities: 0,
    bestProfit: 0,
    avgProfit: 0,
    polymarketMarkets: 0,
    kalshiMarkets: 0,
    matchedMarkets: 0,
    executableOpportunities: 0,
    polymarketStatus: 'ok',
    kalshiStatus: 'ok',
    kalshiConfigured: true // public API — no credentials needed
  };

  const [polyResult, kalshiResult] = await Promise.allSettled([
    fetchPolymarketMarkets(),
    fetchKalshiMarkets()
  ]);

  let polyMarkets = [];
  let kalshiMarkets = [];

  if (polyResult.status === 'fulfilled') {
    polyMarkets = polyResult.value;
    stats.polymarketMarkets = polyMarkets.length;
  } else {
    stats.polymarketStatus = polyResult.reason.message;
    console.error('Polymarket error:', polyResult.reason.message);
  }

  if (kalshiResult.status === 'fulfilled') {
    kalshiMarkets = kalshiResult.value;
    stats.kalshiMarkets = kalshiMarkets.length;
  } else {
    stats.kalshiStatus = kalshiResult.reason.message || 'error';
    console.error('Kalshi error:', msg);
  }

  if (polyMarkets.length === 0 || kalshiMarkets.length === 0) {
    return { opportunities: [], stats };
  }

  const matches = findMatches(polyMarkets, kalshiMarkets);
  stats.matchedMarkets = matches.length;

  const opportunities = [];

  for (const { poly, kalshi, score } of matches) {
    const arbs = calcArb(poly, kalshi);
    for (const arb of arbs) {
      opportunities.push({
        id: `${poly.id}__${kalshi.id}`,
        polyQuestion: poly.question,
        kalshiQuestion: kalshi.question,
        matchScore: score,
        polyUrl: poly.url,
        kalshiUrl: kalshi.url,
        polyVolume: poly.volume,
        polyVolume24hr: poly.volume24hr,
        polyLiquidity: poly.liquidity,
        kalshiVolume: kalshi.volume,
        polyYesMid: poly.yesMid || poly.yesPrice,
        polyNoMid: poly.noMid || poly.noPrice,
        kalshiYesMid: kalshi.yesMid,
        kalshiNoMid: kalshi.noMid,
        kalshiCategory: kalshi.category,
        ...arb
      });
    }
  }

  opportunities.sort((a, b) => b.profitPct - a.profitPct);

  const executable = opportunities.filter(o => !o.isMidPriceOnly);
  stats.totalOpportunities = opportunities.length;
  stats.executableOpportunities = executable.length;

  if (opportunities.length > 0) {
    stats.bestProfit = Math.max(...opportunities.map(o => o.profitPct));
    stats.avgProfit = opportunities.reduce((s, o) => s + o.profitPct, 0) / opportunities.length;
  }

  return { opportunities, stats };
}
