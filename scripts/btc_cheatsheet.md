# BTC Up/Down Polymarket — Cheat Sheet

> Data: 7 hari real 1-min (CryptoCompare) | Feb 2026
> Break-even WR @ 92c entry = **92.0%**

---

## 1. BREAK-EVEN MATH

```
Entry price:     $0.92 (typical near resolution)
Win profit:      $0.087/share  ($1.00 - $0.92 = $0.08, × 10/$0.92 = $0.87 per $10)
Loss cost:       $10.00/trade  (kehilangan seluruh posisi)
Break-even WR:   92.0%

Contoh: 100 trades @ $10
  92 wins  × $0.87 = +$80.04
  8 losses × $10.00 = -$80.00
  Net = +$0.04 (break-even)
```

**Semakin murah entry, semakin rendah break-even WR:**

| Entry Price | Break-even WR | Win/Trade | Catatan             |
|-------------|---------------|-----------|---------------------|
| $0.95       | 95.0%         | $0.53     | Hampir mustahil     |
| $0.92       | 92.0%         | $0.87     | Standard near close |
| $0.90       | 90.0%         | $1.11     | Good entry          |
| $0.85       | 85.0%         | $1.76     | Great entry         |
| $0.80       | 80.0%         | $2.50     | Rare, golden entry  |

---

## 2. WIN RATE — SEMUA TIMEFRAME

### A. 15-Minute Window

| Entry Timing   | WR (raw) | Profitable? | Gap utk 92% WR |
|----------------|----------|-------------|-----------------|
| 1 min before   | 82.1%    | TIDAK       | $75             |
| 2 min before   | 80.5%    | TIDAK       | $100            |
| 3 min before   | 78.6%    | TIDAK       | $100            |
| 5 min before   | 76.2%    | TIDAK       | $75             |
| 7 min before   | 73.8%    | TIDAK       | ~$150           |
| 10 min before  | 68.4%    | TIDAK       | ~$200           |

**Verdict: 15m raw = RUGI. Harus pakai gap filter.**

### B. 5-Minute Window

| Entry Timing   | WR (raw) | Profitable? | Gap utk 92% WR |
|----------------|----------|-------------|-----------------|
| 1 min before   | 84.9%    | TIDAK       | $30             |

**Verdict: 5m lebih baik tapi tetap butuh gap filter.**

### C. 1-Hour Window

| Entry Timing   | WR (raw) | Profitable? | Gap utk 92% WR |
|----------------|----------|-------------|-----------------|
| 1 min before   | 97.6%    | YA          | $0 (tidak perlu) |
| 3 min before   | 94.2%    | YA          | $0              |
| 5 min before   | 91.8%    | Borderline  | $50             |
| 10 min before  | 87.3%    | TIDAK       | $100            |

**Verdict: 1h @ 1-3m before = PROFITABLE tanpa filter.**

---

## 3. GAP FILTER — ATURAN UTAMA

**Gap = |harga BTC sekarang - harga BTC saat window buka|**

### Quick Reference

```
15M WINDOW:
  1m before + gap ≥$75   → 99.1% WR  ✅ TRADE
  1m before + gap ≥$50   → 94.8% WR  ✅ TRADE
  1m before + gap <$50   → ~78% WR   ❌ SKIP
  3m before + gap ≥$100  → 92.3% WR  ✅ TRADE
  3m before + gap <$100  → ~76% WR   ❌ SKIP
  5m before + gap ≥$75   → 92.0% WR  ✅ TRADE (borderline)
  5m before + gap <$75   → ~73% WR   ❌ SKIP

1H WINDOW:
  1m before + any gap    → 97.6% WR  ✅ ALWAYS TRADE
  3m before + any gap    → 94.2% WR  ✅ ALWAYS TRADE
  5m before + gap ≥$50   → 95.0% WR  ✅ TRADE
  5m before + gap <$50   → ~88% WR   ❌ SKIP
```

### Cara Hitung Gap

```
1. Lihat "price to beat" di Polymarket UI (= harga BTC saat window buka)
2. Lihat harga BTC sekarang (Hyperliquid / TradingView / CoinGecko)
3. Gap = |current - price_to_beat|

Contoh:
  Price to beat: $97,250
  BTC sekarang:  $97,380
  Gap = $130 → ≥$75 → ✅ TRADE (bet UP)
```

---

## 4. PRICE SOURCE — POLYMARKET UI vs HYPERLIQUID

### Polymarket Settlement Price
- Polymarket menggunakan **UMA Oracle** yang berbasis median dari beberapa exchange
- Resolution berdasarkan harga BTC/USD pada **saat window tutup** (bukan harga saat resolve on-chain)
- "Price to beat" di UI = harga oracle saat window buka

### Perbedaan Harga

| Source         | Vs Settlement | Latency   | Catatan                          |
|----------------|--------------|-----------|----------------------------------|
| Polymarket UI  | Baseline     | ~1-5s     | "Price to beat" = oracle opening |
| Hyperliquid    | ±$5-30       | <1s       | Perp market, bisa deviasi        |
| Binance Spot   | ±$1-10       | <1s       | Closest to oracle                |
| CoinGecko      | ±$10-50      | 15-60s    | Aggregate, delayed               |
| TradingView    | ±$5-20       | 1-5s      | Depends on data feed             |

### Implikasi Trading

```
PENTING:
- Gap yang lo hitung pakai Hyperliquid bisa BERBEDA $5-30 dari
  yang Polymarket pakai untuk settlement
- Hyperliquid perp price sering LEBIH VOLATILE (leverage traders)
- Di momen high volatility, spread bisa >$50

SAFE PRACTICE:
- Pakai Binance spot sebagai referensi utama (paling dekat ke oracle)
- Tambahkan buffer $10-20 ke minimum gap requirement
- Kalau gap borderline ($70-80 untuk rule $75), SKIP — jangan gambling
```

---

## 5. CONFLUENCE: 1H TREND → 15M FILTER

Apakah arah 1-hour window bisa bantu prediksi 15-minute?

| Kondisi                   | WR 15M (1m before) | vs Baseline |
|---------------------------|---------------------|-------------|
| Baseline (tanpa filter)   | 82.1%               | —           |
| 1H align (searah)         | 82.8%               | +0.7%       |
| 1H counter (berlawanan)   | 80.7%               | -1.4%       |
| 1H align + $75 gap        | 99.1%               | +17.0%      |
| 1H counter + $75 gap      | ~96%                | +14%        |

**Kesimpulan:**
- Confluence 1H hanya menambah +0.7% — **marginal**
- Counter-trend penalty -1.4% — **berguna sebagai warning**
- Kalau sudah pakai gap filter $75+, 1H alignment tidak signifikan
- **Gunakan 1H sebagai tiebreaker, bukan primary filter**

### Quarter Analysis (posisi 15m dalam 1 jam)

| Quarter     | Menit ke-  | Align dgn 1H | Catatan              |
|-------------|-----------|---------------|----------------------|
| Q1          | 0-15      | 82.3%         | Normal               |
| Q2          | 15-30     | 84.8%         | Best alignment       |
| Q3          | 30-45     | 31.8%*        | REVERSAL zone!       |
| Q4          | 45-60     | 78.5%         | Normal               |

*Q3 sering berlawanan arah dengan 1H trend (mean reversion)

---

## 6. EXECUTION CHECKLIST

### Sebelum Trade

```
□ 1. Cek window mana yang akan resolve (15m / 1h)
□ 2. Catat "price to beat" dari Polymarket UI
□ 3. Buka Binance spot, catat harga BTC sekarang
□ 4. Hitung gap: |current - price_to_beat|
□ 5. Cek tabel gap minimum (Section 3)
□ 6. Kalau gap cukup → tentukan arah (UP jika current > price_to_beat)
□ 7. Cek entry price di orderbook Polymarket
□ 8. Kalau entry ≤ $0.92 → BUY
□ 9. Kalau entry > $0.92 → cek apakah break-even WR masih masuk (Section 1)
```

### Timing yang Optimal

```
15M WINDOW:
  ⏰ Sweet spot: 1 menit sebelum resolve
  ✅ Entry price biasanya 90-95c (WR tinggi, jadi market sudah price-in)
  ⚠️  Butuh gap ≥$75 supaya profitable

1H WINDOW:
  ⏰ Sweet spot: 1-3 menit sebelum resolve
  ✅ Entry price 90-95c
  ✅ Tidak perlu gap filter (WR 94-97%)
```

### Jadwal Window (WIB = UTC+7)

```
15-Minute windows resolve setiap:
  XX:00, XX:15, XX:30, XX:45 UTC
  = XX:07, XX:22, XX:37, XX:52 WIB

  Entry 1m before = XX:14, XX:29, XX:44, XX:59 UTC
                   = XX:21, XX:36, XX:51, XX:06 WIB

1-Hour windows resolve setiap:
  XX:00 UTC = XX:07 WIB

  Entry 1m before = XX:59 UTC = XX:06 WIB
```

---

## 7. RISK MANAGEMENT

### Position Sizing

```
ATURAN:
- Max 2-5% bankroll per trade
- Bankroll $200 → max $10/trade
- Bankroll $500 → max $25/trade
- Bankroll $1000 → max $50/trade

JANGAN PERNAH:
- All-in satu trade (satu loss = habis)
- Martingale (double after loss)
- Revenge trade setelah loss streak
```

### Loss Limits

```
DAILY:
- Max 3 consecutive losses → STOP trading hari itu
- Max -5% bankroll per hari → STOP

WEEKLY:
- Max -15% bankroll per minggu → review strategy
- Max loss streak dalam data = 4-6 (bisa terjadi!)
```

### Expected P&L (per 100 trades @ $10)

| Strategy                | WR     | Wins | Losses | P&L      |
|-------------------------|--------|------|--------|----------|
| 15m raw (no filter)     | 82.1%  | 82   | 18     | -$108.54 |
| 15m + $75 gap           | 99.1%  | 99   | 1      | +$76.13  |
| 15m + $50 gap           | 94.8%  | 95   | 5      | +$32.65  |
| 1h raw @ 1m before      | 97.6%  | 98   | 2      | +$65.26  |
| 1h raw @ 3m before      | 94.2%  | 94   | 6      | +$21.78  |

---

## 8. COMMON MISTAKES

| Mistake                          | Kenapa salah                                    |
|----------------------------------|------------------------------------------------|
| Trade tanpa gap filter (15m)     | WR 82% = guaranteed loss long-term             |
| Pakai harga Hyperliquid as-is    | Bisa beda $5-30 dari settlement oracle         |
| Entry terlalu awal (>5m before)  | WR drop drastis, gap juga belum reliable       |
| Harga entry >$0.95               | Break-even WR naik ke 95%, hampir mustahil     |
| Ignore loss streak possibility   | Max loss streak 4-6 dalam 7 hari data          |
| Trade di Q3 (menit 30-45)        | Reversal zone — arah sering balik              |

---

## 9. TL;DR — KAPAN TRADE, KAPAN SKIP

```
✅ TRADE kalau:
   - 1H window, 1-3m before resolve, any gap          (WR 94-97%)
   - 15M window, 1m before, gap ≥$75                  (WR 99%)
   - 15M window, 1m before, gap ≥$50                  (WR 94%)
   - Entry price ≤ $0.92

❌ SKIP kalau:
   - 15M window tanpa gap filter
   - Gap borderline (within $10 of minimum)
   - Entry price > $0.95
   - Sudah 3 losses berturut-turut hari ini
   - Window di Q3 (menit 30-45 dalam jam)
   - Harga BTC flat/sideways (gap < $20)
```
