# Docker Deployment Guide

## Быстрый старт

1. Создайте файл `.env` на основе `.env.example`:
```bash
cp .env.example .env
```

2. Отредактируйте `.env` и укажите:
   - `BOT_TOKEN` - токен вашего Telegram бота
   - `ADMINS` - список ID администраторов через запятую
   - Параметры БД (если используете PostgreSQL)

3. Запустите все сервисы:
```bash
make start
# или
./start.sh
```

## Makefile команды

Для удобства управления проектом используйте Makefile:

### Основные команды
- `make start` - Запустить все сервисы
- `make stop` - Остановить все сервисы
- `make restart` - Перезапустить все сервисы
- `make rebuild` - Пересобрать образы и перезапустить
- `make status` - Показать статус контейнеров

### Логи
- `make logs` - Логи всех сервисов
- `make logs-bot` - Логи бота
- `make logs-rss` - Логи RSS
- `make logs-db` - Логи базы данных

### Миграции
- `make migrate` - Применить все миграции
- `make migrate-create MESSAGE="описание"` - Создать новую миграцию
- `make migrate-upgrade` - Применить миграции
- `make migrate-downgrade` - Откатить миграции
- `make migrate-history` - Показать историю миграций
- `make migrate-current` - Показать текущую версию

### Управление контейнерами
- `make restart-bot` - Перезапустить только бота
- `make restart-rss` - Перезапустить только RSS
- `make restart-db` - Перезапустить только БД
- `make shell-bot` - Открыть shell в контейнере бота
- `make shell-rss` - Открыть shell в контейнере RSS
- `make shell-db` - Открыть psql в контейнере БД

### Утилиты
- `make clean` - Остановить контейнеры и удалить volumes
- `make prune` - Очистить неиспользуемые Docker ресурсы
- `make stats` - Показать статистику использования ресурсов
- `make help` - Показать справку по всем командам

## Структура сервисов

- **db** - PostgreSQL база данных (если `DB_TYPE=postgres`)
- **bot** - Telegram бот
- **rss** - RSS мониторинг

## Управление (через Makefile)

### Просмотр логов
```bash
make logs        # Все логи
make logs-bot    # Логи бота
make logs-rss    # Логи RSS
make logs-db     # Логи БД
```

### Остановка и перезапуск
```bash
make stop        # Остановить все
make restart     # Перезапустить все
make restart-bot # Перезапустить только бота
make restart-rss # Перезапустить только RSS
```

### Пересборка
```bash
make rebuild     # Пересобрать и перезапустить
make build       # Только собрать образы
```

## Миграции

Миграции выполняются **локально** (не в контейнере) при запуске через `make start` или `./start.sh`.

Для ручного управления миграциями локально:
```bash
make migrate                    # Применить все миграции локально
make migrate-create MESSAGE="описание"  # Создать новую миграцию локально
make migrate-upgrade           # Применить миграции локально
make migrate-downgrade         # Откатить на одну версию локально
make migrate-history           # История миграций
make migrate-current           # Текущая версия
```

**Важно:** Для выполнения миграций локально необходимо:
- Установить зависимости: `pip install -r requirements.txt`
- Для PostgreSQL: иметь доступ к БД (локально или через порт контейнера)
- Для SQLite: файл БД должен быть доступен локально

## Использование SQLite

Если хотите использовать SQLite вместо PostgreSQL, установите в `.env`:
```
DB_TYPE=sqlite
```

В этом случае контейнер `db` не будет запущен, и данные будут храниться в файле `reales_bot.db`.

