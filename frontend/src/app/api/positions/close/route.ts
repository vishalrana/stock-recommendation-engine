import { NextResponse } from 'next/server';
import { createClient } from '@supabase/supabase-js';

export async function POST(request: Request) {
  try {
    const { ticker } = await request.json();
    if (!ticker) {
      return NextResponse.json({ error: 'ticker required' }, { status: 400 });
    }

    const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL;
    // Handle both SERVICE_ROLE_KEY and SERVICE_KEY naming conventions
    const supabaseServiceKey = process.env.SUPABASE_SERVICE_ROLE_KEY || process.env.SUPABASE_SERVICE_KEY;

    if (!supabaseUrl || !supabaseServiceKey) {
      return NextResponse.json({ error: 'Supabase credentials missing' }, { status: 500 });
    }

    const supabase = createClient(supabaseUrl, supabaseServiceKey);

    // 1. Fetch current open signal from signals to update outcome in history
    const { data: openSignals } = await supabase
      .from('signals')
      .select('*')
      .eq('ticker', ticker)
      .eq('status', 'open');

    if (openSignals && openSignals.length > 0) {
      const pos = openSignals[0];
      const entry = pos.entry_price || 0;
      const price = pos.price || entry;
      const returnPct = entry > 0 ? ((price - entry) / entry) * 100 : 0;
      
      const entryDate = pos.entry_date ? new Date(pos.entry_date) : new Date();
      const today = new Date();
      const diffTime = Math.abs(today.getTime() - entryDate.getTime());
      const holdingDays = Math.max(0, Math.floor(diffTime / (1000 * 60 * 60 * 24)));

      // Update outcome in signals_history to closed
      await supabase
        .from('signals_history')
        .update({
          outcome: 'closed',
          outcome_date: today.toISOString().split('T')[0],
          outcome_return_pct: returnPct,
          outcome_holding_days: holdingDays,
        })
        .eq('ticker', ticker)
        .eq('outcome', 'open');
    }

    // 2. Update signals table to closed status
    const { error } = await supabase
      .from('signals')
      .update({
        status: 'closed',
        exit_date: new Date().toISOString().split('T')[0],
        sell_signal: false,
      })
      .eq('ticker', ticker)
      .eq('status', 'open');

    if (error) {
      return NextResponse.json({ error: error.message }, { status: 500 });
    }

    return NextResponse.json({ success: true });
  } catch (err: any) {
    return NextResponse.json({ error: err.message || 'Internal Server Error' }, { status: 500 });
  }
}
