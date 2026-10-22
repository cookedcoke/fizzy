"""Announce new rewards and double points days in the Cooked Coke Discord.

Uses a channel webhook (Discord: channel settings → Integrations → Webhooks → New Webhook → Copy
URL) in DISCORD_WEBHOOK_URL. DISCORD_MENTION is who to ping, e.g. "@everyone", "@here" or a role
as "<@&ROLE_ID>". Posting happens in the background: if Discord is slow or down, nothing in Fizzy
waits or breaks - it's just logged.
"""
import logging
import re
import threading

import httpx

from . import config

log = logging.getLogger("fizzy")


def enabled():
    return bool(config.DISCORD_WEBHOOK_URL)


def _mentions(text):
    """Only allow the pings that were asked for, so a reward's name can't ping anyone."""
    allowed = {"parse": []}
    if "@everyone" in text or "@here" in text:
        allowed["parse"].append("everyone")
    roles = re.findall(r"<@&(\d+)>", text)
    if roles:
        allowed["roles"] = roles
    return allowed


def payload(title, body):
    mention = config.DISCORD_MENTION
    content = f"{mention} " if mention else ""
    link = f"\nCheck your points: https://{config.PUBLIC_HOST}/" if config.PUBLIC_HOST else ""
    return {"content": f"{content}**{title}**\n{body}{link}", "allowed_mentions": _mentions(mention),
            "username": "Cooked Coke Co"}


def announce(title, body):
    """Post in the background. Returns straight away."""
    if not enabled():
        return False
    data = payload(title, body)

    def send():
        try:
            httpx.post(config.DISCORD_WEBHOOK_URL, json=data, timeout=10).raise_for_status()
        except Exception as e:
            log.warning("Discord announcement failed: %s", e)
    threading.Thread(target=send, daemon=True).start()
    return True
