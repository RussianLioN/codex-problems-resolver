# Обращение по повторному HTTP 403 promo MCP

Подготовлено 31 октября 2026 года по
[задаче #16](https://github.com/RussianLioN/codex-problems-resolver/issues/16).
Полные доказательства и ограничения — в
[разборе инцидента](2026-10-31-openai-credit-promo-mcp-403.md).

Адресат: обсуждение
[openai/codex #58197](https://github.com/openai/codex/issues/58197).
Статус: текст подготовлен; отправка в чужой репозиторий не выполнялась.
Нужное действие владельца — разрешить публикацию приведённого ниже текста
от его учётной записи GitHub. Секреты, cookie, параметры challenge, имена
проектов, идентификаторы разговоров и персональные пути исключены.

## Подготовленный текст

Ниже английский текст комментария для разработчиков OpenAI; он не является
утверждением об установленной причине на стороне сервиса.

```text
Additional reproduction on macOS 26.7.2 arm64, with both Codex CLI and the
shared app-server at 0.166.0, and openai-developers@openai-curated-remote
1.3.13. Signed in with ChatGPT; the bundled server declares auth=chatgpt.

The affected endpoint is:
https://chatgpt.com/backend-api/product-account-links/mcp

Observed on 2026-10-31 (UTC):
- 14:36:24-25: a new ordinary terminal client had three promo tools and a
  successful preview_api_credit_promo({}) call in that exact thread.
- 14:50:22: the Mac rebooted.
- 16:26:16: the new shared app-server process started, still version 0.166.0.
- 16:26:20: a new thread/start failed promo initialize with HTTP 403 and a
  Cloudflare HTML challenge.
- 16:26:56: thread/resume in a different project failed the same way.
- 16:34:59: one targeted mcpServerStatus/list per affected thread, with both
  threadId and serverName, returned authStatus=bearerToken, zero tools,
  and toolsError containing the same HTTP 403 challenge.
- 16:36:11: one separate mcpServerStatus/list with serverName but without
  threadId also failed its new connection with HTTP 403 / Cloudflare.

This is not limited to a saved thread or one project. A successful probe at
one time did not establish a persistent recovery. The last check also
distinguishes a current connection failure from cached failed thread state.

No model request is needed to reproduce startup failure. We have not
disabled promo, changed endpoint/UA/network routing, copied browser cookies,
re-authenticated, called link_api_org, or redeemed credits as a workaround.
The failed attempts were not followed by repeated reloads.

Please investigate compatibility of this endpoint's protection/authentication
with the official native MCP client, including fresh app-server startup.
Is there a supported correction that preserves the promo functionality?
We do not know which Cloudflare rule triggered the challenge. Raw HTML and
credentials are intentionally omitted.

This report was prepared by an OpenAI Codex agent on behalf of the repository
owner from local runtime evidence and read-only probes.
```

## Граница завершения

Публикация отчёта не означает исправление. Задача #16 остаётся открытой до
проверки применимого исправления на первоначальном запуске MCP после
перезапуска процесса и на восстановлении сохранённой сессии. Разовый успех,
отключение promo или прохождение браузерной проверки не заменяют этот
результат. Перезапуск общего daemon ради проверки не выполняется при
активной работе других разговоров.
