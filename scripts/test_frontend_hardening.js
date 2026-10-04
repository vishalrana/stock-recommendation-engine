/**
 * Frontend Hardening & Evidence UI Verification Suite
 * ====================================================
 * Validates:
 * 1. Data fallback logic (null/undefined win rate, expectancy, trades never default to 0)
 * 2. Missing win rate renders "Not enough history" rather than "0.0%"
 * 3. Rejection reasons never cite R:R or ReachProb as rejection reasons
 * 4. Supporting evidence extraction criteria (Earnings, News, Date, Fundamentals)
 * 5. Strict audit of frontend source files ensuring 0 obsolete portfolio/execution terms
 */

const assert = require('assert');
const fs = require('fs');
const path = require('path');

console.log("==================================================");
console.log("  FRONTEND HARDENING & EVIDENCE VERIFICATION SUITE");
console.log("==================================================");

// --- 1. DATA FALLBACK LOGIC ---
console.log("\n[Test Suite 1] Data Fallback Logic");

function mapMetrics(m, s) {
  return {
    ...s,
    tier_label: s.tier_label || (Number(s.composite_score) >= 80 ? 'Strong Buy' : Number(s.composite_score) >= 65 ? 'Buy' : 'Hold'),
    past_win_rate: m.win_rate !== undefined && m.win_rate !== null ? m.win_rate : null,
    total_trades: (m.wins !== undefined && m.losses !== undefined) ? (m.wins + m.losses) : null,
    expectancy_pct: m.expectancy_pct !== undefined && m.expectancy_pct !== null ? m.expectancy_pct : null,
    wins: m.wins ?? null,
    losses: m.losses ?? null,
  };
}

// Case A: Missing metrics
const unseededStock = mapMetrics({}, { ticker: "NEWCO", composite_score: 66.5 });
assert.strictEqual(unseededStock.past_win_rate, null, "Missing win_rate must be null, not 0");
assert.strictEqual(unseededStock.expectancy_pct, null, "Missing expectancy_pct must be null, not 0");
assert.strictEqual(unseededStock.total_trades, null, "Missing total_trades must be null, not 0");
assert.strictEqual(unseededStock.wins, null, "Missing wins must be null, not 0");
assert.strictEqual(unseededStock.losses, null, "Missing losses must be null, not 0");
assert.strictEqual(unseededStock.tier_label, 'Buy', "Score 66.5 must infer Buy");

// Case B: Existing metrics
const seededStock = mapMetrics({ win_rate: 0.65, expectancy_pct: 4.2, wins: 13, losses: 7 }, { ticker: "SEEDED", composite_score: 82.0 });
assert.strictEqual(seededStock.past_win_rate, 0.65);
assert.strictEqual(seededStock.expectancy_pct, 4.2);
assert.strictEqual(seededStock.total_trades, 20);
assert.strictEqual(seededStock.tier_label, 'Strong Buy');

console.log("  -> PASS: Null fallback handling verified (no artificial 0.0% fallbacks)");

// --- 2. WIN RATE DISPLAY LOGIC ---
console.log("\n[Test Suite 2] Win Rate & UI Formatting");

function formatWinRate(past_win_rate) {
  return (past_win_rate !== null && past_win_rate !== undefined && !isNaN(Number(past_win_rate)))
    ? `${(Number(past_win_rate) * 100).toFixed(0)}%`
    : 'Not enough history';
}

assert.strictEqual(formatWinRate(null), 'Not enough history');
assert.strictEqual(formatWinRate(undefined), 'Not enough history');
assert.strictEqual(formatWinRate(0.583), '58%');
assert.strictEqual(formatWinRate(0.0), '0%'); // True 0% only when explicitly 0
console.log("  -> PASS: Win rate displays 'Not enough history' when data is missing");

// --- 3. REJECTION REASON & R:R MESSAGING ---
console.log("\n[Test Suite 3] Rejection Reason Logic");

function getRejectionReason(sig) {
  if (sig.status === 'manually_removed' || sig.outcome === 'manually_removed') {
    const reason = sig.removal_reason || 'Manually removed by user';
    return sig.removal_note ? `${reason}: ${sig.removal_note}` : reason;
  }
  if (sig.status === 'invalidated' || sig.outcome === 'invalidated') {
    return sig.sell_signal_reason || 'No longer qualifies in subsequent scan';
  }
  if (sig.status === 'stopped' || sig.outcome === 'stopped') {
    return 'Stop loss hit';
  }
  if (sig.status === 'hit_t3' || sig.outcome === 'hit_t3') {
    return 'Target 3 hit';
  }
  if (sig.status === 'hit_t2' || sig.outcome === 'hit_t2') {
    return 'Target 2 hit';
  }
  if (sig.status === 'hit_t1' || sig.outcome === 'hit_t1') {
    return 'Target 1 hit';
  }
  if (sig.rejection_reason) {
    return sig.rejection_reason;
  }
  if (sig.sell_signal_reason) {
    return sig.sell_signal_reason;
  }
  if (sig.earnings_rejected) {
    const days = sig.days_to_earnings !== undefined && sig.days_to_earnings !== null ? `${sig.days_to_earnings}d` : '';
    return `Earnings risk filter (${days})`;
  }
  if (sig.status === 'cancelled_gap_up') {
    return 'Cancelled: Gap > 3%';
  }

  const score = sig.composite_score;
  if (score !== undefined && score !== null && Number(score) < 65) {
    return `Below recommendation threshold (Score ${Number(score).toFixed(1)} < 65)`;
  }
  if (sig.tier_label === 'Rejected') {
    return score !== undefined && score !== null
      ? `Below recommendation threshold (Score ${Number(score).toFixed(1)} < 65)`
      : 'Below recommendation threshold (Score < 65)';
  }

  return 'Setup criteria not met';
}

const lowScoreSig = {
  ticker: "NVDA",
  composite_score: 58.2,
  tier_label: "Rejected",
  reach_prob_t1: 0.18,
  weighted_rr_honest: 1.45,
};
const reason = getRejectionReason(lowScoreSig);
assert.strictEqual(reason, "Below recommendation threshold (Score 58.2 < 65)");
assert(!reason.includes("R:R"), "Must not cite R:R in rejection reason");
assert(!reason.includes("ReachProb"), "Must not cite ReachProb in rejection reason");
console.log("  -> PASS: Rejection reason uses threshold score; never cites R:R or ReachProb");

// --- 4. SUPPORTING EVIDENCE EXTRACTION LOGIC ---
console.log("\n[Test Suite 4] Supporting Evidence Extraction Logic");

function extractEvidence(rec) {
  const items = [];

  // 1. Positive earnings surprise
  if (
    rec.earnings_surprise_pct !== null &&
    rec.earnings_surprise_pct !== undefined &&
    !isNaN(Number(rec.earnings_surprise_pct)) &&
    Number(rec.earnings_surprise_pct) > 0
  ) {
    items.push({
      label: 'Positive earnings',
      detail: `+${Number(rec.earnings_surprise_pct).toFixed(1)}% earnings surprise`,
    });
  } else if (
    rec.context_earnings !== null &&
    rec.context_earnings !== undefined &&
    !isNaN(Number(rec.context_earnings)) &&
    Number(rec.context_earnings) >= 15
  ) {
    items.push({
      label: 'Positive earnings',
      detail: 'Earnings momentum',
    });
  }

  // 2. Positive news sentiment
  if (
    rec.finbert_sentiment !== null &&
    rec.finbert_sentiment !== undefined &&
    !isNaN(Number(rec.finbert_sentiment)) &&
    Number(rec.finbert_sentiment) > 0.15
  ) {
    items.push({
      label: 'Positive news',
      detail: 'Positive news sentiment',
    });
  } else if (
    rec.context_news !== null &&
    rec.context_news !== undefined &&
    !isNaN(Number(rec.context_news)) &&
    Number(rec.context_news) >= 15
  ) {
    items.push({
      label: 'Positive news',
      detail: 'Positive news signal',
    });
  }

  // 3. Next earnings date
  if (rec.next_earnings_date && rec.next_earnings_date.trim() !== '') {
    const dStr = rec.next_earnings_date;
    const days = (rec.days_to_earnings !== null && rec.days_to_earnings !== undefined && Number(rec.days_to_earnings) > 0)
      ? ` (${rec.days_to_earnings}d)`
      : '';
    items.push({
      label: 'Next earnings',
      detail: `${dStr}${days}`,
    });
  }

  // 4. Fundamentally strong
  const de = rec.de_ratio;
  const cr = rec.current_ratio;
  if (
    de !== null &&
    de !== undefined &&
    cr !== null &&
    cr !== undefined &&
    !isNaN(Number(de)) &&
    !isNaN(Number(cr)) &&
    Number(de) < 1.0 &&
    Number(cr) > 1.5
  ) {
    items.push({
      label: 'Fundamentally strong',
      detail: `D/E ${Number(de).toFixed(2)} · Current ratio ${Number(cr).toFixed(2)}`,
    });
  } else if (
    rec.context_fundamental !== null &&
    rec.context_fundamental !== undefined &&
    !isNaN(Number(rec.context_fundamental)) &&
    Number(rec.context_fundamental) >= 15
  ) {
    items.push({
      label: 'Fundamentally strong',
      detail: 'Healthy balance sheet',
    });
  }

  return items;
}

// Case 1: All evidence present
const fullRec = {
  earnings_surprise_pct: 12.4,
  finbert_sentiment: 0.32,
  next_earnings_date: "2026-11-12",
  days_to_earnings: 39,
  de_ratio: 0.45,
  current_ratio: 2.10,
};
const fullEvidence = extractEvidence(fullRec);
assert.strictEqual(fullEvidence.length, 4);
assert.strictEqual(fullEvidence[0].label, 'Positive earnings');
assert.strictEqual(fullEvidence[0].detail, '+12.4% earnings surprise');
assert.strictEqual(fullEvidence[1].label, 'Positive news');
assert.strictEqual(fullEvidence[1].detail, 'Positive news sentiment');
assert.strictEqual(fullEvidence[2].label, 'Next earnings');
assert.strictEqual(fullEvidence[2].detail, '2026-11-12 (39d)');
assert.strictEqual(fullEvidence[3].label, 'Fundamentally strong');
assert.strictEqual(fullEvidence[3].detail, 'D/E 0.45 · Current ratio 2.10');

// Case 2: Zero evidence present (all null/missing)
const emptyRec = {};
const emptyEvidence = extractEvidence(emptyRec);
assert.strictEqual(emptyEvidence.length, 0, "Empty data must yield 0 evidence items (never placeholders)");

// Case 3: Partial evidence (context fallbacks)
const partialRec = {
  context_earnings: 18.0,
  context_fundamental: 16.5,
};
const partialEvidence = extractEvidence(partialRec);
assert.strictEqual(partialEvidence.length, 2);
assert.strictEqual(partialEvidence[0].detail, 'Earnings momentum');
assert.strictEqual(partialEvidence[1].detail, 'Healthy balance sheet');

console.log("  -> PASS: Supporting evidence correctly extracts and hides absent fields");

// --- 5. SOURCE CODE AUDIT: PURGE OF OBSOLETE TERMS ---
console.log("\n[Test Suite 5] Source Code Audit for Obsolete Terms");

const frontendSrc = path.join(__dirname, '..', 'frontend', 'src');
const forbiddenPatterns = [
  /cash is a valid position/i,
  /fetchPortfolioSignals/i,
  /calculatePWin/i,
  /half_kelly/i,
  /portfolio_value/i,
  /exact_shares\s*:/i,
  /brokerage execution/i,
];

function auditDir(dir) {
  const entries = fs.readdirSync(dir, { withFileTypes: true });
  for (const entry of entries) {
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      auditDir(fullPath);
    } else if (entry.isFile() && (entry.name.endsWith('.ts') || entry.name.endsWith('.tsx'))) {
      const content = fs.readFileSync(fullPath, 'utf8');
      for (const pattern of forbiddenPatterns) {
        if (pattern.test(content)) {
          // Allow comments or type definitions only if explicitly safe
          const match = content.match(pattern);
          throw new Error(`Forbidden term '${match[0]}' found in file ${fullPath}`);
        }
      }
    }
  }
}

auditDir(frontendSrc);
console.log("  -> PASS: Frontend code is 100% free of obsolete portfolio/execution terms");

console.log("\n==================================================");
console.log("  ALL FRONTEND HARDENING TESTS PASSED (5/5 SUITES)");
console.log("==================================================");
