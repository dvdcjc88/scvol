import express from 'express';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';
import { fetchArbitrageOpportunities } from './src/calculator.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const app = express();
const PORT = process.env.PORT || 3000;
const REFRESH_INTERVAL_MS = 2 * 60 * 1000; // 2 minutes

app.use(express.static(join(__dirname, 'public')));
app.use(express.json());

let cache = {
  opportunities: [],
  stats: {
    totalOpportunities: 0,
    bestProfit: 0,
    avgProfit: 0,
    polymarketMarkets: 0,
    kalshiMarkets: 0,
    matchedMarkets: 0,
    polymarketStatus: 'pending',
    kalshiStatus: 'pending'
  },
  lastUpdated: null,
  refreshing: false
};

async function refresh() {
  if (cache.refreshing) return;
  cache.refreshing = true;
  try {
    const data = await fetchArbitrageOpportunities();
    cache = {
      opportunities: data.opportunities,
      stats: data.stats,
      lastUpdated: new Date().toISOString(),
      refreshing: false
    };
    console.log(`[${new Date().toISOString()}] Refreshed: ${data.opportunities.length} arb opportunities across ${data.stats.matchedMarkets} matched markets`);
  } catch (err) {
    cache.refreshing = false;
    console.error('Refresh error:', err.message);
  }
}

app.get('/api/arb', (req, res) => {
  res.json(cache);
});

app.post('/api/refresh', async (req, res) => {
  refresh(); // fire-and-forget
  res.json({ message: 'Refresh triggered' });
});

app.get('/api/health', (req, res) => {
  res.json({ status: 'ok', lastUpdated: cache.lastUpdated, uptime: process.uptime() });
});

app.listen(PORT, async () => {
  console.log(`Arb Scanner running on port ${PORT}`);
  await refresh();
  setInterval(refresh, REFRESH_INTERVAL_MS);
});
