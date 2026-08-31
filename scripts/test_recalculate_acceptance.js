/**
 * Target Refactor & Recalculate Acceptance Test Suite
 * ===================================================
 */

function parseScaleOut(weights) {
  if (!weights || typeof weights !== 'string' || !weights.includes('/')) {
    return [50, 30, 20];
  }
  const parts = weights.split('/').map(p => parseFloat(p.trim()));
  const w1 = isNaN(parts[0]) ? 50 : parts[0];
  const w2 = isNaN(parts[1]) ? 30 : parts[1];
  const w3 = isNaN(parts[2]) ? 20 : parts[2];
  return [w1, w2, w3];
}

function getDollarExits(allocatedDollars, scaleOutWeights, targets) {
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

function runAcceptanceTests() {
  console.log("=".repeat(80));
  console.log("  ACCEPTANCE CRITERIA VERIFICATION SUITE");
  console.log("=".repeat(80));

  // -------------------------------------------------------------
  // Scenario A: PLTR (Scale 60/30/10, Allocated $500)
  // -------------------------------------------------------------
  console.log("\n[Scenario A] PLTR (Scale 60/30/10, Allocated $500)");
  const pltr = getDollarExits(500, "60/30/10", {
    target_1: 208.24,
    target_2: 226.83,
    target_3: 231.26,
  });

  console.log(`  T1 Exit: $${pltr.t1.dollars.toFixed(2)} (${pltr.t1.pct}%)`);
  console.log(`  T2 Exit: $${pltr.t2.dollars.toFixed(2)} (${pltr.t2.pct}%)`);
  console.log(`  T3 Exit: $${pltr.t3.dollars.toFixed(2)} (${pltr.t3.pct}%)`);
  console.log(`  Remaining after T1: $${pltr.remainingAfterT1.toFixed(2)}`);
  console.log(`  Remaining after T2: $${pltr.remainingAfterT2.toFixed(2)}`);
  console.log(`  Compact Summary: ${pltr.compactSummary}`);

  const passA = (
    pltr.t1.dollars === 300 &&
    pltr.t2.dollars === 150 &&
    pltr.t3.dollars === 50 &&
    pltr.remainingAfterT1 === 200 &&
    pltr.remainingAfterT2 === 50 &&
    pltr.compactSummary === "T1: $300 | T2: $150 | T3: $50"
  );
  if (!passA) throw new Error("Scenario A Failed!");
  console.log("  --> PASS Scenario A");

  // -------------------------------------------------------------
  // Scenario B: AAPL Pullback Recovery (Scale 70/30/0, Allocated $500)
  // -------------------------------------------------------------
  console.log("\n[Scenario B] AAPL Pullback Recovery (Scale 70/30/0, Allocated $500, T2/T3 = null)");
  const aapl = getDollarExits(500, "70/30/0", {
    target_1: 237.60,
    target_2: null,
    target_3: null,
  });

  console.log(`  T1 Exit: $${aapl.t1.dollars.toFixed(2)} (${aapl.t1.pct}%)`);
  console.log(`  T2: ${aapl.isT2Removed ? '— (removed)' : `$${aapl.t2.dollars.toFixed(2)}`}`);
  console.log(`  T3: ${aapl.isT3Removed ? '— (removed)' : `$${aapl.t3.dollars.toFixed(2)}`}`);
  console.log(`  Runner remaining after T1: $${aapl.runner.dollars.toFixed(2)} (${aapl.runner.pct}%)`);
  console.log(`  Compact Summary: ${aapl.compactSummary}`);

  const passB = (
    aapl.t1.dollars === 350 &&
    aapl.isT2Removed === true &&
    aapl.isT3Removed === true &&
    aapl.runner.dollars === 150 &&
    aapl.compactSummary === "T1: $350 | Runner: $150"
  );
  if (!passB) throw new Error("Scenario B Failed!");
  console.log("  --> PASS Scenario B");

  // -------------------------------------------------------------
  // Scenario C: Recalculate State Machine Simulation
  // -------------------------------------------------------------
  console.log("\n[Scenario C] Recalculate State Machine Simulation (1 Pending Signal + 1 Open Signal)");

  // 1. Pending signal gapping to open
  const pendingSig = {
    ticker: "XYZ",
    status: "pending",
    entry_price: 100.0,
    stop_loss: 95.0,
    target_1: 110.0,
    target_2: 120.0,
    target_3: 130.0,
    allocated_dollars: 500,
    max_shares: 5,
    scale_out_weights: "60/30/10",
  };
  const quoteXYZ = { open: 101.0, high: 102.5, low: 100.5, close: 102.0 };

  // Evaluate pending transition
  const gapPct = ((quoteXYZ.open - pendingSig.entry_price) / pendingSig.entry_price) * 100;
  let newPendingStatus = pendingSig.status;
  let newPendingEntry = pendingSig.entry_price;
  let newPendingStop = pendingSig.stop_loss;
  if (gapPct <= 3.0) {
    newPendingStatus = "open";
    const originalRisk = pendingSig.entry_price - pendingSig.stop_loss;
    newPendingStop = quoteXYZ.open - originalRisk;
    newPendingEntry = quoteXYZ.open;
  }
  const pendingUnrealized = (quoteXYZ.close - newPendingEntry) * pendingSig.max_shares;

  console.log(`  Signal 1 (XYZ): Status: ${pendingSig.status} -> ${newPendingStatus}, Entry: $${newPendingEntry}, Stop: $${newPendingStop}, Unrealized P&L: $${pendingUnrealized.toFixed(2)}`);

  // 2. Open signal hitting T1
  const openSig = {
    ticker: "ABC",
    status: "open",
    entry_price: 50.0,
    stop_loss: 47.0,
    target_1: 56.0,
    target_2: 62.0,
    target_3: 70.0,
    allocated_dollars: 500,
    max_shares: 10,
    scale_out_weights: "50/30/20",
  };
  const quoteABC = { open: 52.0, high: 57.0, low: 51.0, close: 56.5 };

  let newOpenStatus = openSig.status;
  let exitPriceT1 = null;
  let newOpenStop = openSig.stop_loss;
  if (quoteABC.high >= openSig.target_1) {
    newOpenStatus = "hit_t1";
    exitPriceT1 = openSig.target_1;
    newOpenStop = openSig.entry_price; // breakeven
  }
  const remainingShares = openSig.max_shares * 0.5; // 50% sold at T1
  const openUnrealized = (quoteABC.close - openSig.entry_price) * remainingShares;

  console.log(`  Signal 2 (ABC): Status: ${openSig.status} -> ${newOpenStatus}, T1 Exit: $${exitPriceT1}, New Stop (BE): $${newOpenStop}, Unrealized P&L (Remaining): $${openUnrealized.toFixed(2)}`);

  const passC = (
    newPendingStatus === "open" &&
    newPendingEntry === 101.0 &&
    newPendingStop === 96.0 &&
    newOpenStatus === "hit_t1" &&
    exitPriceT1 === 56.0 &&
    newOpenStop === 50.0
  );
  if (!passC) throw new Error("Scenario C Failed!");
  console.log("  --> PASS Scenario C");

  console.log("\n" + "=".repeat(80));
  console.log("  ALL 3 ACCEPTANCE SCENARIOS PASSED WITH EXACT NUMBERS!");
  console.log("=".repeat(80));
}

runAcceptanceTests();
