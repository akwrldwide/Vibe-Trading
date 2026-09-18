import os
import requests
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

class TelegramNotifier:
    """
    Sends formatted crypto strategy alerts (Signals, Breakeven, Exits) to Telegram.
    Uses Telegram Bot API endpoint via standard HTTPS POST requests.
    """
    def __init__(self, bot_token: Optional[str] = None, chat_id: Optional[str] = None):
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
        
        # Fallback manual .env parsing
        if (not self.bot_token or not self.chat_id) and os.path.exists(".env"):
            try:
                with open(".env", "r") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("TELEGRAM_BOT_TOKEN="):
                            self.bot_token = self.bot_token or line.split("=", 1)[1].strip('"\'')
                        elif line.startswith("TELEGRAM_CHAT_ID="):
                            self.chat_id = self.chat_id or line.split("=", 1)[1].strip('"\'')
            except Exception:
                pass

        self.api_url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage" if self.bot_token else ""


    def is_configured(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    def send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        if not self.is_configured():
            logger.warning("Telegram Bot Token or Chat ID not configured. Message skipped.")
            return False

        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True
        }

        try:
            response = requests.post(self.api_url, json=payload, timeout=10)
            if response.status_code == 200:
                logger.info("✅ Telegram notification sent successfully.")
                return True
            else:
                logger.error(f"❌ Telegram API Error ({response.status_code}): {response.text}")
                return False
        except Exception as e:
            logger.error(f"❌ Failed to send Telegram notification: {e}")
            return False

    def send_signal_alert(self, symbol: str, action: str, price: float, stop_loss: float, take_profit: float):
        emoji = "🟢 <b>BUY / LONG</b>" if action.upper() == "BUY" else "🔴 <b>SELL / SHORT</b>"
        risk_dist = abs(price - stop_loss)
        tp_dist = abs(take_profit - price)
        rr_ratio = round(tp_dist / risk_dist, 2) if risk_dist > 0 else 2.5

        msg = (
            f"🚀 <b>NEW SIGNAL DETECTED</b>\n\n"
            f"<b>Symbol:</b> <code>{symbol}</code>\n"
            f"<b>Action:</b> {emoji}\n"
            f"<b>Entry Price:</b> <code>${price:,.2f}</code>\n"
            f"<b>Stop Loss (1.5 ATR Structure):</b> <code>${stop_loss:,.2f}</code>\n"
            f"<b>Take Profit Target (1:{rr_ratio} RR):</b> <code>${take_profit:,.2f}</code>\n\n"
            f"⚡ <i>Engine: Strategy 1 Liquidity Sweep & MSS [vibe-trading]</i>"
        )
        return self.send_message(msg)

    def send_breakeven_alert(self, symbol: str, entry_price: float, new_sl: float):
        msg = (
            f"🛡️ <b>BREAKEVEN REACHED (RISK FREE)</b>\n\n"
            f"<b>Symbol:</b> <code>{symbol}</code>\n"
            f"<b>Status:</b> 1:1 RR Target Hit! SL Moved to Entry.\n"
            f"<b>Entry Price:</b> <code>${entry_price:,.2f}</code>\n"
            f"<b>New Active SL:</b> <code>${new_sl:,.2f}</code> (+5 pips locked)\n\n"
            f"🔒 <i>Trade is now 100% Risk Free!</i>"
        )
        return self.send_message(msg)

    def send_exit_alert(self, symbol: str, action: str, exit_price: float, net_pnl: float, status: str):
        emoji = "🎉 <b>TAKE PROFIT HIT</b>" if "TP" in status.upper() else "🛡️ <b>BREAKEVEN EXIT</b>" if "BE" in status.upper() else "❌ <b>STOP LOSS HIT</b>"
        pnl_str = f"+${net_pnl:,.2f}" if net_pnl >= 0 else f"-${abs(net_pnl):,.2f}"

        msg = (
            f"{emoji}\n\n"
            f"<b>Symbol:</b> <code>{symbol}</code>\n"
            f"<b>Type:</b> <code>{action}</code>\n"
            f"<b>Exit Price:</b> <code>${exit_price:,.2f}</code>\n"
            f"<b>Net PnL:</b> <code>{pnl_str}</code>\n"
            f"<b>Status:</b> {status}\n"
        )
        return self.send_message(msg)

    def send_generic_alert(self, text: str):
        msg = (
            f"🔔 <b>TRADINGVIEW ALERT</b>\n\n"
            f"<code>{text}</code>\n\n"
            f"📱 <i>vibe-trading webhook bridge</i>"
        )
        return self.send_message(msg)


