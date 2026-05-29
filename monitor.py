import asyncio
import logging
import os
from datetime import datetime
import httpx
from telegram import Bot
from telegram.constants import ParseMode
from apscheduler.schedulers.asyncio import AsyncIOScheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Конфиг из env ──────────────────────────────────────
BOT_TOKEN        = os.environ["BOT_TOKEN"]
CHAT_ID          = os.environ["CHAT_ID"]               # твой Telegram user ID
OPENAI_KEY       = os.environ.get("OPENAI_API_KEY", "")
ANTHROPIC_KEY    = os.environ.get("ANTHROPIC_API_KEY", "")
OPENROUTER_KEY   = os.environ.get("OPENROUTER_API_KEY", "")
RAILWAY_TOKEN    = os.environ.get("RAILWAY_API_TOKEN", "")
RENDER_TOKEN     = os.environ.get("RENDER_API_TOKEN", "")

# Пороги алертов
OPENAI_MIN       = float(os.environ.get("OPENAI_MIN_BALANCE", "5"))
ANTHROPIC_MIN    = float(os.environ.get("ANTHROPIC_MIN_BALANCE", "5"))
OPENROUTER_MIN   = float(os.environ.get("OPENROUTER_MIN_BALANCE", "3"))

bot = Bot(token=BOT_TOKEN)

# ══════════════════════════════════════════════════════
# ПРОВЕРКИ БАЛАНСОВ
# ══════════════════════════════════════════════════════

async def check_openai() -> dict:
    if not OPENAI_KEY:
        return {"name": "OpenAI", "ok": None, "msg": "ключ не задан"}
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                "https://api.openai.com/v1/dashboard/billing/credit_grants",
                headers={"Authorization": f"Bearer {OPENAI_KEY}"}
            )
        if r.status_code == 200:
            data = r.json()
            balance = data.get("total_available", 0)
            ok = balance > OPENAI_MIN
            return {"name": "OpenAI", "ok": ok, "balance": f"${balance:.2f}", "threshold": f"${OPENAI_MIN}"}
        # Fallback: subscription endpoint
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                "https://api.openai.com/v1/dashboard/billing/subscription",
                headers={"Authorization": f"Bearer {OPENAI_KEY}"}
            )
        if r.status_code == 200:
            return {"name": "OpenAI", "ok": True, "balance": "API доступен (баланс скрыт)", "threshold": "—"}
        return {"name": "OpenAI", "ok": False, "msg": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"name": "OpenAI", "ok": False, "msg": str(e)}


async def check_anthropic() -> dict:
    if not ANTHROPIC_KEY:
        return {"name": "Anthropic", "ok": None, "msg": "ключ не задан"}
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                "https://api.anthropic.com/v1/organizations/usage",
                headers={
                    "x-api-key": ANTHROPIC_KEY,
                    "anthropic-version": "2023-06-01"
                }
            )
        if r.status_code in (200, 404):
            # Нет публичного баланс-endpoint — проверяем доступность ключа
            return {"name": "Anthropic", "ok": True, "balance": "ключ активен", "threshold": "—"}
        return {"name": "Anthropic", "ok": False, "msg": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"name": "Anthropic", "ok": False, "msg": str(e)}


async def check_openrouter() -> dict:
    if not OPENROUTER_KEY:
        return {"name": "OpenRouter", "ok": None, "msg": "ключ не задан"}
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                "https://openrouter.ai/api/v1/auth/key",
                headers={"Authorization": f"Bearer {OPENROUTER_KEY}"}
            )
        if r.status_code == 200:
            data = r.json().get("data", {})
            limit      = data.get("limit")         # None = unlimited
            usage      = data.get("usage", 0)
            limit_remaining = data.get("limit_remaining")
            if limit is None:
                balance_str = f"unlimited (использовано ${usage:.4f})"
                ok = True
            else:
                remaining = limit_remaining or (limit - usage)
                ok = remaining > OPENROUTER_MIN
                balance_str = f"${remaining:.4f} осталось"
            return {"name": "OpenRouter", "ok": ok, "balance": balance_str, "threshold": f"${OPENROUTER_MIN}"}
        return {"name": "OpenRouter", "ok": False, "msg": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"name": "OpenRouter", "ok": False, "msg": str(e)}


# ══════════════════════════════════════════════════════
# ПРОВЕРКИ ДЕПЛОЕВ
# ══════════════════════════════════════════════════════

RAILWAY_GQL = "https://backboard.railway.app/graphql/v2"

async def check_railway() -> list[dict]:
    if not RAILWAY_TOKEN:
        return [{"name": "Railway", "ok": None, "msg": "токен не задан"}]
    query = """
    query {
      me {
        projects {
          edges {
            node {
              name
              services {
                edges {
                  node {
                    name
                    deployments(last: 1) {
                      edges {
                        node {
                          status
                          createdAt
                        }
                      }
                    }
                  }
                }
              }
            }
          }
        }
      }
    }
    """
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                RAILWAY_GQL,
                json={"query": query},
                headers={"Authorization": f"Bearer {RAILWAY_TOKEN}"}
            )
        if r.status_code != 200:
            return [{"name": "Railway", "ok": False, "msg": f"HTTP {r.status_code}"}]
        data = r.json()
        results = []
        for proj_edge in data["data"]["me"]["projects"]["edges"]:
            proj = proj_edge["node"]
            proj_name = proj["name"]
            for svc_edge in proj["services"]["edges"]:
                svc = svc_edge["node"]
                svc_name = svc["name"]
                deploys = svc["deployments"]["edges"]
                if not deploys:
                    results.append({"name": f"{proj_name} / {svc_name}", "ok": None, "msg": "нет деплоев"})
                    continue
                status = deploys[0]["node"]["status"]
                ok = status == "SUCCESS"
                results.append({
                    "name": f"{proj_name} / {svc_name}",
                    "ok": ok,
                    "status": status
                })
        return results
    except Exception as e:
        return [{"name": "Railway", "ok": False, "msg": str(e)}]


async def check_render() -> list[dict]:
    if not RENDER_TOKEN:
        return [{"name": "Render", "ok": None, "msg": "токен не задан"}]
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                "https://api.render.com/v1/services?limit=20",
                headers={"Authorization": f"Bearer {RENDER_TOKEN}"}
            )
        if r.status_code != 200:
            return [{"name": "Render", "ok": False, "msg": f"HTTP {r.status_code}"}]
        services = r.json()
        results = []
        for item in services:
            svc = item.get("service", item)
            name   = svc.get("name", "?")
            state  = svc.get("suspended", "not_suspended")
            deploy = svc.get("deployInfo", {})
            d_status = deploy.get("buildStatus") or deploy.get("status") or "unknown"
            ok = state == "not_suspended" and d_status in ("live", "build_in_progress", "update_in_progress", "unknown")
            results.append({"name": f"Render / {name}", "ok": ok, "status": d_status, "suspended": state})
        return results
    except Exception as e:
        return [{"name": "Render", "ok": False, "msg": str(e)}]


# ══════════════════════════════════════════════════════
# ФОРМАТИРОВАНИЕ СООБЩЕНИЙ
# ══════════════════════════════════════════════════════

def status_icon(ok):
    if ok is True:  return "✅"
    if ok is False: return "🔴"
    return "⚪"

def fmt_balance(r: dict) -> str:
    icon = status_icon(r["ok"])
    name = r["name"]
    if "balance" in r:
        thresh = f" (порог: {r['threshold']})" if r.get("threshold", "—") != "—" else ""
        return f"{icon} *{name}*: {r['balance']}{thresh}"
    return f"{icon} *{name}*: {r.get('msg', '?')}"

def fmt_deploy(r: dict) -> str:
    icon = status_icon(r["ok"])
    name = r["name"]
    if "status" in r:
        return f"{icon} {name}: `{r['status']}`"
    return f"{icon} {name}: {r.get('msg', '?')}"

async def build_digest(balances, deploys_rw, deploys_rd) -> str:
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    lines = [f"🌅 *Утренний дайджест* — {now}\n"]

    lines.append("*💰 Балансы API*")
    for b in balances:
        lines.append(fmt_balance(b))

    lines.append("\n*🚂 Railway деплои*")
    for d in deploys_rw:
        lines.append(fmt_deploy(d))

    lines.append("\n*🎨 Render сервисы*")
    for d in deploys_rd:
        lines.append(fmt_deploy(d))

    # Итог
    all_checks = balances + deploys_rw + deploys_rd
    failed = [x for x in all_checks if x["ok"] is False]
    if failed:
        lines.append(f"\n⚠️ *Проблем найдено: {len(failed)}*")
    else:
        lines.append("\n✅ *Всё в порядке*")

    return "\n".join(lines)


# ══════════════════════════════════════════════════════
# ЗАДАЧИ ПЛАНИРОВЩИКА
# ══════════════════════════════════════════════════════

_last_failed: set = set()   # для дедупликации алертов

async def run_all_checks():
    balances  = await asyncio.gather(check_openai(), check_anthropic(), check_openrouter())
    deploys_rw = await check_railway()
    deploys_rd = await check_render()
    return list(balances), deploys_rw, deploys_rd

async def morning_digest():
    log.info("Running morning digest")
    try:
        balances, deploys_rw, deploys_rd = await run_all_checks()
        msg = await build_digest(balances, deploys_rw, deploys_rd)
        await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        log.error(f"Digest error: {e}")
        await bot.send_message(chat_id=CHAT_ID, text=f"❌ Ошибка дайджеста: {e}")

async def alert_check():
    """Каждые 30 мин — только алерты если что упало"""
    global _last_failed
    log.info("Running alert check")
    try:
        balances, deploys_rw, deploys_rd = await run_all_checks()
        all_checks = list(balances) + deploys_rw + deploys_rd
        failed_now = {x["name"] for x in all_checks if x["ok"] is False}

        # Новые падения (которых раньше не было)
        new_failures = failed_now - _last_failed
        # Восстановления
        recovered = _last_failed - failed_now

        if new_failures:
            lines = ["🚨 *АЛЕРТ — что-то упало!*\n"]
            for name in new_failures:
                item = next(x for x in all_checks if x["name"] == name)
                lines.append(fmt_deploy(item) if "status" in item else fmt_balance(item))
            await bot.send_message(chat_id=CHAT_ID, text="\n".join(lines), parse_mode=ParseMode.MARKDOWN)

        if recovered:
            lines = ["✅ *Восстановлено*\n"]
            for name in recovered:
                lines.append(f"✅ {name}")
            await bot.send_message(chat_id=CHAT_ID, text="\n".join(lines), parse_mode=ParseMode.MARKDOWN)

        _last_failed = failed_now
    except Exception as e:
        log.error(f"Alert check error: {e}")


# ══════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════

async def main():
    log.info("Monitor bot starting...")

    # Проверяем что бот работает
    me = await bot.get_me()
    log.info(f"Bot: @{me.username}")
    await bot.send_message(chat_id=CHAT_ID, text=f"🤖 Монитор запущен (@{me.username})\nПервый дайджест придёт в 9:00")

    scheduler = AsyncIOScheduler(timezone="Europe/Madrid")
    scheduler.add_job(morning_digest, "cron", hour=9, minute=0)
    scheduler.add_job(alert_check,   "interval", minutes=30)
    scheduler.start()

    # Сразу запускаем проверку при старте
    await morning_digest()

    # Держим процесс живым
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
