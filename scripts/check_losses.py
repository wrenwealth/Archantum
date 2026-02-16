"""Quick script to check paper trade losses."""
import sqlite3
from datetime import datetime, timedelta

conn = sqlite3.connect("archantum.db")
cur = conn.execute(
    "SELECT id, window_start, direction, win, pnl_usd, btc_price_at_open, "
    "btc_price_at_entry, gap_usd, confidence, hour_zone, resolved_direction "
    "FROM paper_trades "
    "WHERE win = 0 AND resolved_direction IS NOT NULL AND resolved_direction != 'INCONCLUSIVE' "
    "ORDER BY window_start"
)
rows = cur.fetchall()
print(f"Total losses: {len(rows)}\n")
for r in rows:
    tid, ws, d, w, pnl, bopen, bentry, gap, conf, zone, res = r
    if isinstance(ws, str):
        dt = datetime.fromisoformat(ws)
    else:
        dt = datetime.utcfromtimestamp(ws)
    et = dt - timedelta(hours=5)
    wib = dt + timedelta(hours=7)
    print(
        f"#{tid:>3} | {et.strftime('%H:%M')} ET / {wib.strftime('%H:%M')} WIB | "
        f"{d:>4} -> {res:>4} | gap ${gap:+.0f} | PnL ${pnl:+.2f} | {conf} | {zone}"
    )
conn.close()
