"use client";

// Display-only position-sizing guidance (profitability program, Phase 6). The account size and
// risk per idea live only in this browser's localStorage: never sent to the server, never stored
// in Supabase, and never used by the engine. Nothing here places or suggests an order.
import { useEffect, useState } from 'react';

const KEY_ACCOUNT = 'sizing_account_usd';
const KEY_RISK = 'sizing_risk_pct';
export const DEFAULT_RISK_PCT = 0.5;

function read(key: string): number | null {
  try {
    const v = window.localStorage.getItem(key);
    const n = v === null ? NaN : Number(v);
    return Number.isFinite(n) && n > 0 ? n : null;
  } catch {
    return null;
  }
}

function write(key: string, value: number | null) {
  try {
    if (value === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, String(value));
  } catch {
    // storage blocked (private window): keep the value for this page view only
  }
}

export function useSizing() {
  const [account, setAccountState] = useState<number | null>(null);
  const [riskPct, setRiskState] = useState<number>(DEFAULT_RISK_PCT);
  useEffect(() => {
    setAccountState(read(KEY_ACCOUNT));
    setRiskState(read(KEY_RISK) ?? DEFAULT_RISK_PCT);
  }, []);
  return {
    account,
    riskPct,
    setAccount: (v: number | null) => { setAccountState(v); write(KEY_ACCOUNT, v); },
    setRiskPct: (v: number) => { setRiskState(v); write(KEY_RISK, v); },
  };
}

export interface SizingGuide {
  shares: number;
  riskUsd: number;
  positionUsd: number;
  cappedByAccount: boolean;
}

/** Shares such that a stop-out loses about riskPct of the account, capped at the account size. */
export function sizeIdea(account: number | null, riskPct: number, entry?: number | null,
                         stop?: number | null): SizingGuide | null {
  if (!account || !entry || !stop || !(entry > stop) || !(riskPct > 0)) return null;
  const perShare = entry - stop;
  let shares = Math.floor((account * riskPct / 100) / perShare);
  const maxShares = Math.floor(account / entry);
  const capped = shares > maxShares;
  if (capped) shares = maxShares;
  if (shares <= 0) return null;
  return { shares, riskUsd: shares * perShare, positionUsd: shares * entry, cappedByAccount: capped };
}
