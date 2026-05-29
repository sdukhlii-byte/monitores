# Monitor Bot

Мониторит балансы API и статусы деплоев. Шлёт утренний дайджест в 9:00 (Europe/Madrid) и алерты сразу при падении.

## Что мониторит
- **Балансы**: OpenAI, Anthropic, OpenRouter
- **Деплои**: все Railway сервисы (оба аккаунта если добавить второй токен), Render сервисы

## Деплой на Railway

1. Создай новый бот через @BotFather → скопируй токен
2. Узнай свой Telegram ID — напиши @userinfobot
3. Создай Railway API token: railway.com/account/tokens
4. Создай Render API token: render.com/account/settings
5. Задеплой:

```bash
railway login
railway link  # выбери all-projects
railway up --service monitor-bot
```

6. Добавь переменные из .env.example в Railway Variables

## Добавить второй Railway аккаунт
Добавь переменную `RAILWAY_API_TOKEN_2` и в коде раскомментируй второй вызов check_railway().
