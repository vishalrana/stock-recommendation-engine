import { parseScaleOut, getDollarExits, getSharesForScaleOut } from '../frontend/src/lib/position-utils';

function runTests() {
  console.log("=".repeat(80));
  console.log("  TESTING POSITION UTILS & SCALE-OUT DOLLAR EXITS");
  console.log("=".repeat(80));

  // Scenario A: PLTR
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

  if (
    pltr.t1.dollars === 300 &&
    pltr.t2.dollars === 150 &&
    pltr.t3.dollars === 50 &&
    pltr.remainingAfterT1 === 200 &&
    pltr.remainingAfterT2 === 50
  ) {
    console.log("  --> PASS Scenario A");
  } else {
    console.error("  --> FAIL Scenario A");
    process.exit(1);
  }

  // Scenario B: AAPL Pullback Recovery
  console.log("\n[Scenario B] AAPL Pullback Recovery (Scale 70/30/0, Allocated $500, T2/T3 = null)");
  const aapl = getDollarExits(500, "70/30/0", {
    target_1: 237.60,
    target_2: null,
    target_3: null,
  });

  console.log(`  T1 Exit: $${aapl.t1.dollars.toFixed(2)} (${aapl.t1.pct}%)`);
  console.log(`  T2 Removed: ${aapl.isT2Removed}, Dollars: $${aapl.t2.dollars.toFixed(2)}`);
  console.log(`  T3 Removed: ${aapl.isT3Removed}, Dollars: $${aapl.t3.dollars.toFixed(2)}`);
  console.log(`  Runner Remaining after T1: $${aapl.runner.dollars.toFixed(2)} (${aapl.runner.pct}%)`);
  console.log(`  Compact Summary: ${aapl.compactSummary}`);

  if (
    aapl.t1.dollars === 350 &&
    aapl.isT2Removed === true &&
    aapl.isT3Removed === true &&
    aapl.runner.dollars === 150 &&
    aapl.compactSummary === "T1: $350 | Runner: $150"
  ) {
    console.log("  --> PASS Scenario B");
  } else {
    console.error("  --> FAIL Scenario B");
    process.exit(1);
  }

  console.log("\n" + "=".repeat(80));
  console.log("  ALL ACCEPTANCE SCENARIOS PASSED PERFECTLY!");
  console.log("=".repeat(80));
}

runTests();
