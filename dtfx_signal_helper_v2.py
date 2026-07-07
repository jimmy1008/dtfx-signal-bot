#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DTFX Signal Helper v2 - 完整版本
✓ 連接真實 Hyperliquid API
✓ 每小時自動掃描 BTC/ETH
✓ 推送候選信號到 Telegram
✓ 需人工確認敘述和 Order Flow
"""

import asyncio
import aiohttp
import sys
import io
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
import statistics
import json

if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# ============ 配置 ============
TG_BOT_TOKEN = "7926602022:AAFYSC5Yh9VRwWQsx-d26VzPGqCbX26ZzsI"
TG_CHAT_ID = "5686223888"
HYPERLIQUID_API = "https://api.hyperliquid.xyz"

SYMBOLS = ["BTC", "ETH"]
TIMEFRAMES = ["15m", "1h"]  # 15分鐘和1小時
MIN_RR_RATIO = 2.0

# ============ Hyperliquid API ============

async def fetch_hyperliquid_klines(symbol: str, interval: str = "1h", limit: int = 100) -> Optional[List]:
    """
    從 Hyperliquid 獲取 K線數據
    interval: "1m", "5m", "15m", "1h", "4h", "1d"
    """
    try:
        async with aiohttp.ClientSession() as session:
            # Hyperliquid candleSnapshot API
            url = f"{HYPERLIQUID_API}/info"

            payload = {
                "type": "candleSnapshot",
                "req": {
                    "coin": symbol,
                    "interval": interval,
                    "startTime": int((datetime.utcnow() - timedelta(hours=limit)).timestamp() * 1000)
                }
            }

            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    candles = data.get("candles", [])
                    if candles:
                        return candles
    except Exception as e:
        print(f"❌ Hyperliquid API 錯誤 ({symbol}): {e}")

    return None

# ============ DTFX 分析器 ============

class DTFXAnalyzer:
    """DTFX 信號分析器"""

    def __init__(self, candles: List[Dict]):
        # 解析 K線數據
        self.closes = [float(c.get("c", 0)) for c in candles]
        self.highs = [float(c.get("h", 0)) for c in candles]
        self.lows = [float(c.get("l", 0)) for c in candles]
        self.volumes = [float(c.get("v", 0)) for c in candles]
        self.times = [int(c.get("t", 0)) for c in candles]

    def find_swing_points(self, window: int = 5) -> Tuple[List[Tuple[int, float]], List[Tuple[int, float]]]:
        """識別 Swing Highs 和 Swing Lows"""
        swing_highs = []
        swing_lows = []

        for i in range(window, len(self.closes) - window):
            window_highs = self.highs[i-window:i+window+1]
            window_lows = self.lows[i-window:i+window+1]

            if self.highs[i] == max(window_highs):
                swing_highs.append((i, self.highs[i]))

            if self.lows[i] == min(window_lows):
                swing_lows.append((i, self.lows[i]))

        return swing_highs, swing_lows

    def detect_structure(self) -> Dict:
        """判讀市場結構"""
        if len(self.closes) < 20:
            return {"structure": "INSUFFICIENT_DATA"}

        recent = self.closes[-20:]
        swing_highs, swing_lows = self.find_swing_points(window=3)

        if not swing_highs or not swing_lows:
            return {"structure": "RANGE"}

        # 檢查最近的高低點
        recent_highs = [h for i, h in swing_highs if i >= len(self.closes) - 20]
        recent_lows = [l for i, l in swing_lows if i >= len(self.closes) - 20]

        if len(recent_highs) >= 2 and len(recent_lows) >= 2:
            # HH/HL 或 LH/LL
            hh = recent_highs[-1] > recent_highs[-2]
            ll = recent_lows[-1] < recent_lows[-2]
            hl = recent_highs[-1] < recent_highs[-2]
            lh = recent_lows[-1] > recent_lows[-2]

            if hh and ll:
                structure = "UPTREND"
            elif hl and lh:
                structure = "DOWNTREND"
            else:
                structure = "RANGE"
        else:
            structure = "RANGE"

        return {
            "structure": structure,
            "recent_high": max(recent),
            "recent_low": min(recent),
            "latest_price": self.closes[-1]
        }

    def identify_zones(self) -> Dict[str, Dict]:
        """識別關鍵 Zones"""
        swing_highs, swing_lows = self.find_swing_points(window=3)
        zones = {}

        if swing_highs:
            last_high_idx, last_high_price = swing_highs[-1]
            zone_width = abs(self.closes[-1] - last_high_price) * 0.015
            zones["swing_high"] = {
                "level": round(last_high_price, 2),
                "low": round(last_high_price - zone_width, 2),
                "high": round(last_high_price + zone_width, 2),
                "distance_from_current": round(abs(self.closes[-1] - last_high_price), 2),
                "bars_ago": len(self.closes) - last_high_idx
            }

        if swing_lows:
            last_low_idx, last_low_price = swing_lows[-1]
            zone_width = abs(self.closes[-1] - last_low_price) * 0.015
            zones["swing_low"] = {
                "level": round(last_low_price, 2),
                "low": round(last_low_price - zone_width, 2),
                "high": round(last_low_price + zone_width, 2),
                "distance_from_current": round(abs(self.closes[-1] - last_low_price), 2),
                "bars_ago": len(self.closes) - last_low_idx
            }

        return zones

    def analyze_order_flow_proxy(self) -> Dict:
        """Order Flow 代理信號"""
        if len(self.closes) < 5:
            return {"trend": "UNKNOWN", "momentum": "WEAK"}

        recent = self.closes[-5:]
        moves = [recent[i] - recent[i-1] for i in range(1, len(recent))]
        avg_move = statistics.mean(moves)
        volatility = max(recent) - min(recent)

        trend = "UP" if avg_move > 0 else "DOWN"
        momentum = "STRONG" if abs(avg_move) > volatility * 0.08 else "WEAK"

        return {
            "avg_move": round(avg_move, 4),
            "volatility": round(volatility, 2),
            "trend": trend,
            "momentum": momentum,
            "volume_trend": "UP" if self.volumes[-1] > statistics.mean(self.volumes[-5:]) else "DOWN"
        }

    def generate_signals(self) -> List[Dict]:
        """生成候選信號"""
        signals = []

        structure = self.detect_structure()
        zones = self.identify_zones()
        flow = self.analyze_order_flow_proxy()

        if structure["structure"] == "INSUFFICIENT_DATA" or not zones:
            return signals

        current = self.closes[-1]

        # ===== UPTREND - BOUNCE BUY =====
        if structure["structure"] == "UPTREND" and "swing_low" in zones:
            swing_low = zones["swing_low"]
            distance_pct = (swing_low["distance_from_current"] / current) * 100

            if distance_pct < 3 and flow["trend"] == "UP" and flow["momentum"] == "STRONG":
                entry = round(current, 2)
                sl = round(swing_low["level"] * 0.98, 2)
                tp = round(structure["recent_high"] * 1.01, 2)

                rr = abs(tp - entry) / abs(entry - sl) if entry != sl else 0

                if rr >= MIN_RR_RATIO:
                    signals.append({
                        "type": "BOUNCE_BUY",
                        "structure": "UPTREND",
                        "entry": entry,
                        "stop_loss": sl,
                        "take_profit": tp,
                        "rr_ratio": round(rr, 2),
                        "zone": f"Swing Low @ {swing_low['level']}",
                        "confidence": "⭐⭐⭐" if rr > 3 else "⭐⭐",
                        "flow": flow
                    })

        # ===== DOWNTREND - REJECTION SELL =====
        elif structure["structure"] == "DOWNTREND" and "swing_high" in zones:
            swing_high = zones["swing_high"]
            distance_pct = (swing_high["distance_from_current"] / current) * 100

            if distance_pct < 3 and flow["trend"] == "DOWN" and flow["momentum"] == "STRONG":
                entry = round(current, 2)
                sl = round(swing_high["level"] * 1.02, 2)
                tp = round(structure["recent_low"] * 0.99, 2)

                rr = abs(entry - tp) / abs(sl - entry) if entry != sl else 0

                if rr >= MIN_RR_RATIO:
                    signals.append({
                        "type": "REJECTION_SELL",
                        "structure": "DOWNTREND",
                        "entry": entry,
                        "stop_loss": sl,
                        "take_profit": tp,
                        "rr_ratio": round(rr, 2),
                        "zone": f"Swing High @ {swing_high['level']}",
                        "confidence": "⭐⭐⭐" if rr > 3 else "⭐⭐",
                        "flow": flow
                    })

        return signals

# ============ Telegram ============

async def send_signal_to_telegram(symbol: str, timeframe: str, signal: Dict):
    """推送信號到 Telegram"""

    msg = f"""
<b>🔍 DTFX Signal Helper</b>

<b>標的:</b> {symbol} {timeframe}
<b>信號:</b> {signal['type']}
<b>結構:</b> {signal['structure']}
<b>信心:</b> {signal['confidence']}

<b>📊 AI 計算結果</b>
進場: <code>{signal['entry']}</code>
止損: <code>{signal['stop_loss']}</code>
止盈: <code>{signal['take_profit']}</code>
<b>R:R = 1:{signal['rr_ratio']}</b>

<b>🎯 Zone</b>
{signal['zone']}

<b>✋ 需要你確認 3 點</b>
1️⃣ 敘述是否支持？（是否在反彈/反跌？）
2️⃣ Order Flow 確認？（成交量/動量？）
3️⃣ 進場點清晰？

⏰ {datetime.utcnow().strftime('%H:%M UTC')}
    """

    try:
        async with aiohttp.ClientSession() as session:
            url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
            payload = {
                "chat_id": TG_CHAT_ID,
                "text": msg,
                "parse_mode": "HTML"
            }
            async with session.post(url, json=payload) as resp:
                return resp.status == 200
    except Exception as e:
        print(f"❌ Telegram 錯誤: {e}")
        return False

# ============ 主掃描 ============

async def scan_market():
    """掃描市場"""

    print(f"\n{'='*70}")
    print(f"🤖 DTFX Signal Helper v2 - 15m & 1h")
    print(f"⏰ {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*70}\n")

    total_signals = 0

    for symbol in SYMBOLS:
        print(f"📊 掃描 {symbol}...")

        for timeframe in TIMEFRAMES:
            # K線數量根據時間框架調整
            limit = 100 if timeframe == "1h" else 300  # 15m 需要更多根數據

            # 獲取 K線
            candles = await fetch_hyperliquid_klines(symbol, timeframe, limit=limit)

            if not candles or len(candles) < 20:
                print(f"  ⚠️  {timeframe}: 無足夠數據")
                continue

            # 分析
            analyzer = DTFXAnalyzer(candles)
            signals = analyzer.generate_signals()

            if signals:
                print(f"  ✓ {timeframe}: 發現 {len(signals)} 個候選信號")

                for sig in signals:
                    # 推送 Telegram
                    timeframe_display = "15 分鐘" if timeframe == "15m" else "1 小時"
                    success = await send_signal_to_telegram(symbol, timeframe_display, sig)
                    if success:
                        print(f"    → {sig['type']} ({timeframe}) | R:R {sig['rr_ratio']} ✓ 已推送")
                        total_signals += 1
                    else:
                        print(f"    ✗ 推送失敗")
            else:
                print(f"  - {timeframe}: 無符合條件的信號")

    print(f"\n{'='*70}")
    if total_signals > 0:
        print(f"✅ 發現 {total_signals} 個候選信號 | 請在 Telegram 確認")
    else:
        print(f"📌 無高勝率信號 | 繼續監視")
    print(f"{'='*70}\n")

# ============ 入口 ============

if __name__ == "__main__":
    print("🚀 啟動 DTFX Signal Helper v2")
    asyncio.run(scan_market())
