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

BOT_TOKEN        = os.environ["BOT_TOKEN"]
CHAT_ID          = os.environ["CHAT_ID"]
OPENAI_KEY       = os.environ.get("OPENAI_API_KEY", "")
ANTHROPIC_KEY    = os.environ.get("ANTHROPIC_API_KEY", "")
OPENROUTER_KEY   = os.environ.get("OPENROUTER_API_KEY", "")        # GG Group
OPENROUTER_KEY2  = os.environ.get("OPENROUTER_API_KEY_2", "")      # Личный
OPENROUTER_MGMT  = os.environ.get("OPENROUTER_MGMT_KEY", "")          # Management key для баланса
RAILWAY_TOKEN    = os.environ.get("RAILWAY_API_TOKEN", "")
RAILWAY_TOKEN_2  = os.environ.get("RAILWAY_API_TOKEN_2", "")
RENDER_TOKEN     = os.environ.get("RENDER_API_TOKEN", "")

OPENAI_MIN       = float(os.environ.get("OPENAI_MIN_BALANCE", "5"))
ANTHROPIC_MIN    = float(os.environ.get("ANTHROPIC_MIN_BALANCE", "5"))
OPENROUTER_MIN   = float(os.environ.get("OPENROUTER_MIN_BALANCE", "5"))

bot = Bot(token=BOT_TOKEN)

# ══════════════════════════════════════════════════════
# БАЛАНСЫ
# ══════════════════════════════════════════════════════

async def check_openai() -> dict:
    if not OPENAI_KEY:
        return {"name": "OpenAI", "ok": None, "msg": "ключ не задан"}
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            # Новый endpoint через usage API
            r = await c.get(
                "https://api.openai.com/v1/organization/usage/completions?start_time=1700000000",
                headers={"Authorization": f"Bearer {OPENAI_KEY}"}
            )
        if r.status_code == 200:
            return {"name": "OpenAI", "ok": True, "balance": "ключ активен (баланс через dashboard)"}
        # Fallback — просто проверяем что ключ валидный
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {OPENAI_KEY}"}
            )
        if r.status_code == 200:
            return {"name": "OpenAI", "ok": True, "balance": "ключ активен"}
        return {"name": "OpenAI", "ok": False, "msg": f"HTTP {r.status_code} — ключ не работает"}
    except Exception as e:
        return {"name": "OpenAI", "ok": False, "msg": str(e)}


async def check_anthropic() -> dict:
    if not ANTHROPIC_KEY:
        return {"name": "Anthropic", "ok": None, "msg": "ключ не задан"}
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json"
                },
                json={"model": "claude-haiku-4-5-20251001", "max_tokens": 1, "messages": [{"role": "user", "content": "hi"}]}
            )
        # 200 = ок, 400 = ок (ключ валиден), 401 = невалидный ключ
        if r.status_code in (200, 400):
            return {"name": "Anthropic", "ok": True, "balance": "ключ активен"}
        return {"name": "Anthropic", "ok": False, "msg": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"name": "Anthropic", "ok": False, "msg": str(e)}


async def _openrouter_balance(mgmt_key: str) -> dict:
    """Получает реальный баланс через Management Key."""
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                "https://openrouter.ai/api/v1/credits",
                headers={"Authorization": f"Bearer {mgmt_key}"}
            )
        if r.status_code != 200:
            return {"ok": False, "msg": f"credits API HTTP {r.status_code}"}
        data = r.json().get("data", r.json())
        total   = float(data.get("total_credits", 0) or 0)
        used    = float(data.get("total_usage", 0) or 0)
        balance = total - used
        ok = balance > OPENROUTER_MIN
        return {"ok": ok, "balance": f"${balance:.2f} осталось (потрачено ${used:.2f} из ${total:.2f})", "threshold": f"${OPENROUTER_MIN}"}
    except Exception as e:
        return {"ok": False, "msg": str(e)}


async def _check_openrouter_key(key: str, label: str) -> dict:
    """Проверяет один OpenRouter ключ — валидность + usage."""
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                "https://openrouter.ai/api/v1/auth/key",
                headers={"Authorization": f"Bearer {key}"}
            )
        if r.status_code != 200:
            return {"name": f"OpenRouter ({label})", "ok": False, "msg": f"HTTP {r.status_code}"}
        data  = r.json().get("data", {})
        usage = float(data.get("usage", 0) or 0)
        return {"name": f"OpenRouter ({label})", "ok": True, "balance": f"ключ активен, использовано ${usage:.2f}"}
    except Exception as e:
        return {"name": f"OpenRouter ({label})", "ok": False, "msg": str(e)}


async def check_openrouter() -> list[dict]:
    results = []
    # Если есть management key — показываем реальный баланс аккаунта
    if OPENROUTER_MGMT:
        b = await _openrouter_balance(OPENROUTER_MGMT)
        results.append({"name": "OpenRouter (баланс аккаунта)", **b})
    # Проверяем валидность ключей
    if OPENROUTER_KEY:
        results.append(await _check_openrouter_key(OPENROUTER_KEY, "GG Group"))
    if OPENROUTER_KEY2:
        results.append(await _check_openrouter_key(OPENROUTER_KEY2, "Личный"))
    if not results:
        results.append({"name": "OpenRouter", "ok": None, "msg": "ключи не заданы"})
    return results


# ══════════════════════════════════════════════════════
# ДЕПЛОИ
# ══════════════════════════════════════════════════════

RAILWAY_GQL = "https://backboard.railway.app/graphql/v2"

RAILWAY_QUERY = """
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
                    node { status createdAt }
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

async def _check_railway_token(token: str, label: str) -> list[dict]:
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                RAILWAY_GQL,
                json={"query": RAILWAY_QUERY},
                headers={"Authorization": f"Bearer {token}"}
            )
        if r.status_code != 200:
            return [{"name": f"Railway ({label})", "ok": False, "msg": f"HTTP {r.status_code}"}]
        data = r.json()
        projects = (data.get("data") or {}).get("me", {}).get("projects", {}).get("edges", [])
        results = []
        for proj_edge in projects:
            proj = proj_edge.get("node") or {}
            proj_name = proj.get("name", "?")
            for svc_edge in proj.get("services", {}).get("edges", []):
                svc = svc_edge.get("node") or {}
                svc_name = svc.get("name", "?")
                if svc_name.lower() in ("redis", "postgres", "postgresql"):
                    continue
                deploys = svc.get("deployments", {}).get("edges", [])
                if not deploys:
                    continue
                status = (deploys[0].get("node") or {}).get("status", "UNKNOWN")
                ok = status == "SUCCESS"
                results.append({
                    "name": f"{proj_name} / {svc_name}",
                    "ok": ok,
                    "status": status
                })
        return results
    except Exception as e:
        return [{"name": f"Railway ({label})", "ok": False, "msg": str(e)}]


async def check_railway() -> list[dict]:
    results = []
    if RAILWAY_TOKEN:
        results += await _check_railway_token(RAILWAY_TOKEN, "sdukhlii")
    if RAILWAY_TOKEN_2:
        results += await _check_railway_token(RAILWAY_TOKEN_2, "GG Group")
    return results or [{"name": "Railway", "ok": None, "msg": "токены не заданы"}]


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
        results = []
        for item in r.json():
            svc      = item.get("service", item)
            name     = svc.get("name", "?")
            suspended = svc.get("suspended", "not_suspended")
            ok       = suspended == "not_suspended"
            results.append({
                "name": f"Render / {name}",
                "ok": ok,
                "status": "active" if ok else "suspended"
            })
        return results
    except Exception as e:
        return [{"name": "Render", "ok": False, "msg": str(e)}]


# ══════════════════════════════════════════════════════
# ФОРМАТИРОВАНИЕ
# ══════════════════════════════════════════════════════

def icon(ok):
    if ok is True:  return "✅"
    if ok is False: return "🔴"
    return "⚪"

def fmt_balance(r: dict) -> str:
    i = icon(r["ok"])
    if "balance" in r:
        thresh = f" (порог {r['threshold']})" if r.get("threshold") else ""
        return f"{i} *{r['name']}*: {r['balance']}{thresh}"
    return f"{i} *{r['name']}*: {r.get('msg', '?')}"

def fmt_deploy(r: dict) -> str:
    i = icon(r["ok"])
    if "status" in r:
        return f"{i} {r['name']}: `{r['status']}`"
    return f"{i} {r['name']}: {r.get('msg', '?')}"

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

    all_checks = balances + deploys_rw + deploys_rd
    failed = [x for x in all_checks if x["ok"] is False]
    if failed:
        lines.append(f"\n⚠️ *Проблем найдено: {len(failed)}*")
    else:
        lines.append("\n✅ *Всё в порядке*")

    return "\n".join(lines)


# ══════════════════════════════════════════════════════
# ПЛАНИРОВЩИК
# ══════════════════════════════════════════════════════

_last_failed: set = set()

async def run_all_checks():
    openrouter_results = await check_openrouter()
    balances = list(await asyncio.gather(check_openai(), check_anthropic())) + openrouter_results
    deploys_rw = await check_railway()
    deploys_rd = await check_render()
    return balances, deploys_rw, deploys_rd

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
    global _last_failed
    log.info("Running alert check")
    try:
        balances, deploys_rw, deploys_rd = await run_all_checks()
        all_checks = balances + deploys_rw + deploys_rd
        failed_now = {x["name"] for x in all_checks if x["ok"] is False}
        new_failures = failed_now - _last_failed
        recovered    = _last_failed - failed_now

        if new_failures:
            lines = ["🚨 *АЛЕРТ — что-то упало!*\n"]
            for name in new_failures:
                item = next(x for x in all_checks if x["name"] == name)
                lines.append(fmt_deploy(item) if "status" in item else fmt_balance(item))
            await bot.send_message(chat_id=CHAT_ID, text="\n".join(lines), parse_mode=ParseMode.MARKDOWN)

        if recovered:
            lines = ["✅ *Восстановлено*\n"] + [f"✅ {n}" for n in recovered]
            await bot.send_message(chat_id=CHAT_ID, text="\n".join(lines), parse_mode=ParseMode.MARKDOWN)

        _last_failed = failed_now
    except Exception as e:
        log.error(f"Alert check error: {e}")


async def main():
    log.info("Monitor bot starting...")
    me = await bot.get_me()
    log.info(f"Bot: @{me.username}")
    await bot.send_message(chat_id=CHAT_ID, text=f"🤖 Монитор запущен (@{me.username})\nДайджест в 9:00 по Madrid")

    scheduler = AsyncIOScheduler(timezone="Europe/Madrid")
    scheduler.add_job(morning_digest, "cron", hour=9, minute=0)
    scheduler.add_job(alert_check,   "interval", minutes=30)
    scheduler.start()

    await morning_digest()

    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
