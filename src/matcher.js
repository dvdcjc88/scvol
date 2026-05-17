const STOP_WORDS = new Set([
  'will', 'the', 'a', 'an', 'be', 'is', 'are', 'was', 'were', 'been',
  'have', 'has', 'had', 'do', 'does', 'did', 'would', 'could', 'should',
  'may', 'might', 'shall', 'can', 'must', 'need', 'ought', 'going',
  'to', 'of', 'in', 'for', 'on', 'with', 'at', 'by', 'from', 'up',
  'about', 'into', 'through', 'during', 'before', 'after', 'above',
  'below', 'between', 'out', 'off', 'over', 'under', 'again', 'further',
  'then', 'once', 'here', 'there', 'when', 'where', 'why', 'how',
  'all', 'both', 'each', 'few', 'more', 'most', 'other', 'some', 'such',
  'no', 'nor', 'not', 'only', 'own', 'same', 'so', 'than', 'too', 'very',
  'just', 'and', 'or', 'but', 'if', 'as', 'what', 'which', 'who', 'this',
  'that', 'these', 'those', 'it', 'its', 'i', 'he', 'she', 'they',
  'we', 'you', 'my', 'your', 'his', 'her', 'their', 'our', 'any',
  'get', 'gets', 'got', 'end', 'ends', 'least', 'ever', 'even', 'still',
  'within', 'market', 'event', 'chance', 'probability', 'likely',
  'happen', 'happens', 'occur', 'occurs', 'come', 'comes', 'make', 'made',
  'reach', 'hit', 'least', 'most', 'least', 'above', 'below', 'over',
  'under', 'between', 'least', 'most', 'many', 'much', 'more', 'than'
]);

function tokenize(text) {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, ' ')
    .split(/\s+/)
    .filter(w => w.length >= 3 && !STOP_WORDS.has(w) && !/^\d{1,2}$/.test(w));
}

function jaccardSimilarity(tokens1, tokens2) {
  if (tokens1.size === 0 || tokens2.size === 0) return 0;
  let intersection = 0;
  for (const t of tokens1) {
    if (tokens2.has(t)) intersection++;
  }
  const union = tokens1.size + tokens2.size - intersection;
  return intersection / union;
}

// Check if any token from set1 is a prefix/suffix of any token in set2 (handles plurality, etc.)
function partialMatch(tokens1, tokens2) {
  let count = 0;
  for (const t1 of tokens1) {
    for (const t2 of tokens2) {
      if (t1 !== t2 && t1.length >= 4 && t2.length >= 4) {
        if (t1.startsWith(t2.slice(0, -1)) || t2.startsWith(t1.slice(0, -1))) {
          count++;
        }
      }
    }
  }
  return count;
}

export function findMatches(polyMarkets, kalshiMarkets, threshold = 0.18) {
  const kalshiTokenCache = kalshiMarkets.map(m => ({
    market: m,
    tokens: new Set(tokenize(m.question))
  }));

  const matches = [];

  for (const poly of polyMarkets) {
    const polyTokens = new Set(tokenize(poly.question));
    if (polyTokens.size < 2) continue;

    for (const { market: kalshi, tokens: kalshiTokens } of kalshiTokenCache) {
      if (kalshiTokens.size < 2) continue;

      let score = jaccardSimilarity(polyTokens, kalshiTokens);

      // Boost score for partial word matches (handles plurals, verb forms)
      const partial = partialMatch(polyTokens, kalshiTokens);
      if (partial > 0) score += partial * 0.05;

      if (score >= threshold) {
        matches.push({ poly, kalshi, score });
      }
    }
  }

  // Remove duplicate poly+kalshi pairings, keep highest score
  const seen = new Map();
  for (const m of matches) {
    const key = `${m.poly.id}::${m.kalshi.id}`;
    if (!seen.has(key) || seen.get(key).score < m.score) {
      seen.set(key, m);
    }
  }

  return [...seen.values()].sort((a, b) => b.score - a.score);
}
