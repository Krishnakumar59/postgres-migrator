import os
import sys
import json
import subprocess
import urllib.request
from datetime import datetime
from rich.console import Console

console = Console()

class MigrationNotifier:
    """
    Multi-Channel Notification Engine.
    Dispatches real-time alerts across:
      1. Slack Webhooks
      2. Discord Webhooks (with rich embeds)
      3. Telegram Bot Push Alerts (Mobile)
      4. OS Desktop Push Notifications (Linux notify-send / macOS AppleScript)
    """

    def __init__(self, webhook_url: str = None):
        self.webhook_url = webhook_url or os.getenv("MIGRATION_WEBHOOK_URL") or os.getenv("DISCORD_WEBHOOK_URL") or os.getenv("SLACK_WEBHOOK_URL")
        self.telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")

    def _send_desktop_notification(self, title: str, message: str, status: str = "info"):
        """Triggers local OS desktop push notifications."""
        try:
            if sys.platform.startswith("linux"):
                # Linux libnotify
                urgency = "critical" if status == "error" else "normal"
                subprocess.run(["notify-send", "-u", urgency, title, message], capture_output=True, timeout=2)
            elif sys.platform == "darwin":
                # macOS AppleScript
                script = f'display notification "{message}" with title "{title}"'
                subprocess.run(["osascript", "-e", script], capture_output=True, timeout=2)
        except Exception:
            pass

    def _send_telegram_notification(self, title: str, message: str, status: str = "info"):
        """Sends mobile push alert via Telegram Bot API."""
        if not self.telegram_bot_token or not self.telegram_chat_id:
            return

        emoji = "🚀" if status == "start" else ("✅" if status == "success" else ("⚠️" if status == "warning" else "❌"))
        text = f"{emoji} *{title}*\n\n{message}\n\n_Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}_"

        url = f"https://api.telegram.org/bot{self.telegram_bot_token}/sendMessage"
        payload = {
            "chat_id": self.telegram_chat_id,
            "text": text,
            "parse_mode": "Markdown"
        }

        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                if response.status == 200:
                    console.print("[dim green]✔ Telegram notification dispatched.[/dim green]")
        except Exception as e:
            console.print(f"[dim yellow]⚠️ Telegram notification error: {e}[/dim yellow]")

    def notify(self, title: str, message: str, status: str = "info", details: dict = None):
        """Dispatches notification across all configured channels."""
        # 1. Desktop Notification
        self._send_desktop_notification(title, message, status)

        # 2. Telegram Alert
        self._send_telegram_notification(title, message, status)

        # 3. Slack / Discord Webhook Alert
        if not self.webhook_url:
            return

        emoji = "🚀" if status == "start" else ("✅" if status == "success" else ("⚠️" if status == "warning" else "❌"))

        # Discord Rich Embed Formatting
        if "discord.com" in self.webhook_url:
            color = 0x2ECC71 if status == "success" else (0xE74C3C if status == "error" else (0xF1C40F if status == "warning" else 0x3498DB))
            fields = []
            if details:
                for k, v in details.items():
                    fields.append({"name": str(k), "value": str(v), "inline": True})

            payload = {
                "embeds": [{
                    "title": f"{emoji} {title}",
                    "description": message,
                    "color": color,
                    "fields": fields,
                    "footer": {"text": f"PG-Migrator • {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"}
                }]
            }
        else:
            # Slack / Standard Webhook
            blocks = [
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": f"{emoji} {title}"}
                },
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": message}
                }
            ]
            if details:
                fields = [{"type": "mrkdwn", "text": f"*{k}:*\n{v}"} for k, v in details.items()]
                blocks.append({"type": "section", "fields": fields[:10]})

            payload = {
                "text": f"{emoji} *{title}*\n{message}",
                "blocks": blocks
            }

        try:
            req = urllib.request.Request(
                self.webhook_url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json", "User-Agent": "PostgresMigrator/1.0"}
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                if response.status in (200, 204):
                    console.print("[dim green]✔ Webhook notification dispatched.[/dim green]")
        except Exception as e:
            console.print(f"[dim yellow]⚠️ Webhook notification warning: {e}[/dim yellow]")

    def test_alert(self) -> bool:
        """Sends a test notification to verify all configured channels."""
        console.print("\n[bold cyan]🔔 Testing Notification Channels...[/bold cyan]")
        self.notify(
            title="PG-Migrator Test Alert",
            message="Your notification channels (Discord, Slack, Telegram, Desktop) are configured properly and working!",
            status="info",
            details={"Environment": "Test Run", "Status": "Connected", "Version": "PostgreSQL Migrator 1.0"}
        )
        return True
