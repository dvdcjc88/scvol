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
  'reach', 'hit', 'many', 'much', 'than'
]);

function tokenize(text) {
  return text
    .toLowerCase()
    // Preserve hyphenated codes as one token: CO-01 → co01, WI-05 → wi05
    .replace(/([a-z0-9])-([a-z0-9])/g, '$1$2')
    .replace(/[^a-z0-9\s]/g, ' ')
    .split(/\s+/)
    .filter(w => w.length >= 3 && !STOP_WORDS.has(w));
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

export function findMatches(polyMarkets, kalshiMarkets, threshold = 0.3) {
  const kalshiTokenCache = kalshiMarkets.map(m => ({
    market: m,
    tokens: new Set(tokenize(m.question))
  }));

  const allMatches = [];

  for (const poly of polyMarkets) {
    const polyTokens = new Set(tokenize(poly.question));
    if (polyTokens.size < 2) continue;

    for (const { market: kalshi, tokens: kalshiTokens } of kalshiTokenCache) {
      if (kalshiTokens.size < 2) continue;

      let score = jaccardSimilarity(polyTokens, kalshiTokens);
      const partial = partialMatch(polyTokens, kalshiTokens);
      if (partial > 0) score += partial * 0.05;

      if (score >= threshold) {
        allMatches.push({ poly, kalshi, score });
      }
    }
  }

  // Sort by score descending then enforce greedy 1-to-1 matching:
  // each Polymarket and each Kalshi market appears in at most one pair.
  allMatches.sort((a, b) => b.score - a.score);

  const usedPoly   = new Set();
  const usedKalshi = new Set();
  const result     = [];

  for (const match of allMatches) {
    if (!usedPoly.has(match.poly.id) && !usedKalshi.has(match.kalshi.id)) {
      usedPoly.add(match.poly.id);
      usedKalshi.add(match.kalshi.id);
      result.push(match);
    }
  }

  return result;
}
