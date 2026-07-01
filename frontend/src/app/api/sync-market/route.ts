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
    if (!marketState.open) {
      return NextResponse.json({
        success: false,
        error: 'Market is currently closed.',
        reason: marketState.reason || 'Outside trading hours (4:00 AM - 8:00 PM ET, Mon-Fri)'
      }, { status: 403 });
    }
    
    console.log('[API-SYNC] Running centralized evaluation loop...');
    const result = await evaluate_open_positions();
    
    return NextResponse.json({
      success: true,
      timestamp: new Date().toISOString(),
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
