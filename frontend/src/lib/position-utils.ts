/**
 * Position & Scale-Out Math Utilities
 * ====================================
 * Calculates dollar distributions, fractional shares, and surviving target allocations
 * across multi-tier scale-out strategies (e.g. 50/30/20, 60/30/10, 70/30/0).
 */

export interface DollarExitInfo {
  dollars: number;
  pct: number;
  targetPrice: number | null;
  isRemoved: boolean;
}

export interface DollarExitBreakdown {
  t1: DollarExitInfo;
  t2: DollarExitInfo;
  t3: DollarExitInfo;
  runner: DollarExitInfo;
  remainingAfterT1: number;
  remainingAfterT2: number;
  totalAllocated: number;
  scaleOutWeights: string;
  isT2Removed: boolean;
  isT3Removed: boolean;
  compactSummary: string;
}

/**
 * Parse a scale-out string like "50/30/20", "60/30/10", or "70/30/0" into percentage numbers.
 */
export function parseScaleOut(weights: string | null | undefined): [number, number, number] {
  if (!weights || typeof weights !== 'string' || !weights.includes('/')) {
    return [50, 30, 20];
  }
  const parts = weights.split('/').map(p => parseFloat(p.trim()));
  const w1 = isNaN(parts[0]) ? 50 : parts[0];
  const w2 = isNaN(parts[1]) ? 30 : parts[1];
  const w3 = isNaN(parts[2]) ? 20 : parts[2];
  return [w1, w2, w3];
}

/**
 * Compute the precise dollar distribution across T1, T2, and T3 (or runner).
 */
export function getDollarExits(
  allocatedDollars: number,
  scaleOutWeights: string | null | undefined,
  targets?: {
    target_1?: number | null;
    target_2?: number | null;
    target_3?: number | null;
  }
): DollarExitBreakdown {
  const totalAllocated = Math.max(0, Number(allocatedDollars) || 0);
  const normalizedWeightsStr = scaleOutWeights || '50/30/20';
  const [w1, w2, w3] = parseScaleOut(normalizedWeightsStr);

  const isT2Removed = targets !== undefined && (targets.target_2 === null || targets.target_2 === undefined);
  const isT3Removed = targets !== undefined && (targets.target_3 === null || targets.target_3 === undefined);

  const t1ExitDollars = Math.round((w1 / 100) * totalAllocated * 100) / 100;
  const t2ExitDollars = isT2Removed ? 0 : Math.round((w2 / 100) * totalAllocated * 100) / 100;
  const t3ExitDollars = isT3Removed ? 0 : Math.round((w3 / 100) * totalAllocated * 100) / 100;

  const remainingAfterT1 = Math.max(0, Math.round((totalAllocated - t1ExitDollars) * 100) / 100);
  const remainingAfterT2 = Math.max(0, Math.round((remainingAfterT1 - (isT2Removed ? 0 : (w2 / 100) * totalAllocated)) * 100) / 100);

  // Runner dollars when T2/T3 are pruned
  const runnerDollars = isT2Removed
    ? remainingAfterT1
    : isT3Removed
    ? remainingAfterT2
    : 0;

  let compactSummary = '';
  if (isT2Removed) {
    compactSummary = `T1: $${t1ExitDollars.toFixed(0)} | Runner: $${runnerDollars.toFixed(0)}`;
  } else if (isT3Removed) {
    compactSummary = `T1: $${t1ExitDollars.toFixed(0)} | T2: $${t2ExitDollars.toFixed(0)} | Runner: $${runnerDollars.toFixed(0)}`;
  } else {
    compactSummary = `T1: $${t1ExitDollars.toFixed(0)} | T2: $${t2ExitDollars.toFixed(0)} | T3: $${t3ExitDollars.toFixed(0)}`;
  }

  return {
    t1: {
      dollars: t1ExitDollars,
      pct: w1,
      targetPrice: targets?.target_1 ?? null,
      isRemoved: false,
    },
    t2: {
      dollars: t2ExitDollars,
      pct: isT2Removed ? 0 : w2,
      targetPrice: targets?.target_2 ?? null,
      isRemoved: isT2Removed,
    },
    t3: {
      dollars: t3ExitDollars,
      pct: isT3Removed ? 0 : w3,
      targetPrice: targets?.target_3 ?? null,
      isRemoved: isT3Removed,
    },
    runner: {
      dollars: runnerDollars,
      pct: isT2Removed ? 100 - w1 : isT3Removed ? 100 - w1 - w2 : 0,
      targetPrice: null,
      isRemoved: !isT2Removed && !isT3Removed,
    },
    remainingAfterT1,
    remainingAfterT2,
    totalAllocated,
    scaleOutWeights: normalizedWeightsStr,
    isT2Removed,
    isT3Removed,
    compactSummary,
  };
}

/**
 * Calculate share counts for a given scale-out step.
 */
export function getSharesForScaleOut(
  maxShares: number,
  weightPct: number
): number {
  if (!maxShares || maxShares <= 0) return 0;
  return Math.floor((maxShares * weightPct) / 100);
}
