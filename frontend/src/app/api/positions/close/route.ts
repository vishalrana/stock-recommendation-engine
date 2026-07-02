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

    const { data: openSignals } = await supabase
      .from('signals')
      .select('*')
      .eq('ticker', ticker)
      .eq('status', 'open')
      .limit(1);

    if (!openSignals || openSignals.length === 0) {
      return NextResponse.json({ error: 'open position not found' }, { status: 404 });
    }

    const pos = openSignals[0];
    const exitPrice = Number(pos.price || pos.entry_price || 0);
    const { data, error } = await supabase.rpc('execute_position_exit', {
      p_signal_id: String(pos.id),
      p_exit_price: exitPrice,
      p_outcome: 'closed',
      p_reason: 'Manual close',
      p_split_fraction: 1,
      p_live_price: exitPrice,
      p_move_stop_to_entry: false
    });

    if (error) {
      return NextResponse.json({ error: error.message }, { status: 500 });
    }

    return NextResponse.json({ success: true, result: data });
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : 'Internal Server Error';
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
