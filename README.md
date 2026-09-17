# FILSON UA → Telegram (GitHub Actions, без Terminal)

Этот проект проверяет каждые 5 минут:

- OLX
- Shafa
- Prom
- Bigl

и отправляет новые объявления Filson в Telegram.

## Как хранится история

Файл `data/seen.json` содержит уже увиденные объявления.
GitHub Actions автоматически обновляет его после запуска.

На первом запуске проект использует **warm start**:
все объявления, которые уже существуют, будут записаны в базу,
но не будут отправлены в Telegram. После этого отправляются только новые.

---

# ВАЖНО: полностью бесплатный режим

Для проверки каждые 5 минут лучше создать **PUBLIC repository**.

Код будет публичным, но:

- `TELEGRAM_BOT_TOKEN` хранится только в GitHub Secrets;
- `TELEGRAM_CHAT_ID` хранится только в GitHub Secrets;
- секреты НЕ находятся в этом ZIP;
- `.env` здесь не нужен.

Никогда не записывайте Telegram token прямо в файлы репозитория.

---

# Установка полностью через браузер

## Шаг 1. Создать репозиторий

Откройте:

https://github.com/new

Repository name:

`filson-ua-monitor`

Выберите:

**Public**

Нажмите:

**Create repository**

---

## Шаг 2. Загрузить файлы из ZIP

Распакуйте ZIP.

В GitHub откройте:

**Add file → Upload files**

Загрузите:

- `monitor.py`
- `requirements.txt`
- `sources.yaml`
- папку `data` с `seen.json`

Затем нажмите **Commit changes**.

### Workflow

В macOS папка `.github` может быть скрыта.

Поэтому в архиве также есть видимый файл:

`WORKFLOW_COPY_THIS_TO_GITHUB.yml`

На GitHub нажмите:

**Add file → Create new file**

В поле имени файла напишите ТОЧНО:

`.github/workflows/filson-monitor.yml`

Откройте локальный файл `WORKFLOW_COPY_THIS_TO_GITHUB.yml`,
скопируйте всё его содержимое и вставьте на GitHub.

Нажмите **Commit changes**.

---

# Шаг 3. Добавить Telegram Secrets

В репозитории откройте:

**Settings → Secrets and variables → Actions**

Нажмите:

**New repository secret**

Создайте два секрета.

### Первый

Name:

`TELEGRAM_BOT_TOKEN`

Secret:

ваш новый token от @BotFather

### Второй

Name:

`TELEGRAM_CHAT_ID`

Secret:

ваш Telegram chat_id

Они должны называться ТОЧНО так.

---

# Шаг 4. Дать Actions право обновлять seen.json

Откройте:

**Settings → Actions → General**

Найдите:

**Workflow permissions**

Выберите:

**Read and write permissions**

Нажмите **Save**.

Это позволяет боту сохранять базу уже увиденных объявлений.

---

# Шаг 5. Первый запуск

Перейдите во вкладку:

**Actions**

Слева выберите:

**Filson UA Monitor**

Нажмите:

**Run workflow → Run workflow**

Первый запуск может занять несколько минут,
потому что GitHub устанавливает Chromium.

При первом запуске Telegram-сообщения со старыми объявлениями
НЕ отправляются. Они только попадают в `data/seen.json`.

---

# Шаг 6. Готово

После этого workflow будет запускаться автоматически по cron:

`*/5 * * * *`

То есть расписание настроено на каждые 5 минут.

Важно: GitHub не гарантирует старт ровно в секунду по расписанию.
При высокой нагрузке scheduled workflow иногда может запускаться
с небольшой задержкой.

Mac можно выключить — мониторинг выполняется на серверах GitHub.

---

# Проверить, что всё работает

Откройте:

**Actions → Filson UA Monitor**

Последние запуски должны быть зелёными.

Внутри run будут строки примерно:

- `Scanning OLX`
- `Scanning Shafa`
- `Scanning Prom`
- `Scanning Bigl`
- `Sent 0 new listings`

`Sent 0 new listings` означает, что новых объявлений после предыдущего
запуска не появилось.

---

# Добавить новый украинский сайт

Откройте `sources.yaml` на GitHub и добавьте:

```yaml
  - name: Example
    enabled: true
    url: "https://example.ua/search?q=filson"
    domain: "example.ua"
```

После Commit следующий запуск попробует мониторить новый источник.

Можно использовать:

```yaml
exclude_any:
  - "реплика"
  - "акумулятор"
```

или:

```yaml
include_any:
  - "куртка"
  - "сумка"
  - "жилет"
```

---

# Если сайт показывает CAPTCHA

Проект намеренно не обходит CAPTCHA и другие антибот-защиты.

Если конкретная площадка начнёт блокировать GitHub Actions,
её потребуется либо исключить, либо заменить официальным способом
уведомлений/API, если он существует.

---

# Если Telegram token когда-нибудь попал на скриншот или в публичный файл

Сразу:

1. откройте @BotFather;
2. отзовите старый token;
3. получите новый;
4. замените только GitHub Secret `TELEGRAM_BOT_TOKEN`.

Не нужно менять код.
