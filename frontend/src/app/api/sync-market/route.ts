import { NextResponse } from 'next/server';
import { is_us_market_open, evaluate_open_positions } from '../../../lib/market-evaluator';

export const dynamic = 'force-dynamic';

export async function POST() {
  return handleSync();
}

export async function GET() {
  return handleSync();
}

async function handleSync() {
  try {
    const marketState = is_us_market_open();
    console.log(`[API-SYNC] Running market evaluation (market open: ${marketState.open})...`);
    
    // Always run position evaluation & live price sync so prices update even after-hours
    const result = await evaluate_open_positions();
    
    return NextResponse.json({
      success: true,
      timestamp: new Date().toISOString(),
      marketOpen: marketState.open,
      marketReason: marketState.reason || (marketState.open ? 'Market is open' : 'Market is closed'),
      summary: result
    });
  } catch (err: any) {
    console.error('[API-SYNC] Error executing market evaluation:', err);
    return NextResponse.json({
      success: false,
      error: err.message || 'Internal Server Error'
    }, { status: 500 });
  }
}
