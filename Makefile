.PHONY: help start stop restart rebuild logs logs-bot logs-rss logs-db status clean migrate migrate-create migrate-upgrade migrate-downgrade shell-bot shell-rss shell-db build down up setup

help: ## Показать справку по командам
	@echo "Доступные команды:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

setup: ## Установить права на выполнение для скриптов
	@echo "🔧 Установка прав на выполнение..."
	@chmod +x start.sh
	@chmod +x docker-entrypoint.sh
	@echo "✅ Права установлены"

start: setup ## Запустить все сервисы
	@echo "🚀 Запуск всех сервисов..."
	@./start.sh

stop: ## Остановить все сервисы
	@echo "🛑 Остановка всех сервисов..."
	@docker-compose down

restart: ## Перезапустить все сервисы
	@echo "🔄 Перезапуск всех сервисов..."
	@docker-compose restart

rebuild: ## Пересобрать образы и перезапустить
	@echo "🔨 Пересборка образов..."
	@docker-compose build --no-cache
	@echo "🚀 Запуск сервисов..."
	@docker-compose up -d

build: ## Собрать образы без кеша
	@echo "🔨 Сборка образов..."
	@docker-compose build --no-cache

up: ## Запустить сервисы в фоне
	@echo "🚀 Запуск сервисов в фоне..."
	@docker-compose up -d

down: ## Остановить и удалить контейнеры
	@echo "🛑 Остановка и удаление контейнеров..."
	@docker-compose down

logs: ## Показать логи всех сервисов
	@docker-compose logs -f

logs-bot: ## Показать логи бота
	@docker-compose logs -f bot

logs-rss: ## Показать логи RSS
	@docker-compose logs -f rss

logs-db: ## Показать логи базы данных
	@docker-compose logs -f db

status: ## Показать статус контейнеров
	@echo "📊 Статус контейнеров:"
	@docker-compose ps

clean: ## Остановить контейнеры и удалить volumes
	@echo "🧹 Очистка контейнеров и volumes..."
	@docker-compose down -v
	@echo "⚠️  Внимание: Все данные в БД будут удалены!"

migrate: ## Применить все миграции в контейнере
	@echo "⬆️  Применение миграций в контейнере bot..."
	@$(DOCKER_COMPOSE_CMD) exec bot alembic upgrade head

migrate-create: ## Создать новую миграцию в контейнере (использовать: make migrate-create MESSAGE="описание")
	@if [ -z "$(MESSAGE)" ]; then \
		echo "❌ Ошибка: Укажите описание миграции"; \
		echo "   Использование: make migrate-create MESSAGE=\"описание\""; \
		exit 1; \
	fi
	@echo "📝 Создание миграции: $(MESSAGE)"
	@$(DOCKER_COMPOSE_CMD) exec bot alembic revision --autogenerate -m "$(MESSAGE)"

migrate-clean-empty: ## Удалить все пустые миграции
	@echo "🧹 Поиск и удаление пустых миграций..."
	@for file in migration/versions/*.py; do \
		if [ -f "$$file" ] && ! grep -q "op\." "$$file" 2>/dev/null; then \
			echo "   Найдена пустая миграция: $$(basename $$file)"; \
			rm -f "$$file"; \
			echo "   ✓ Удалено: $$(basename $$file)"; \
		fi; \
	done; \
	echo "✅ Проверка завершена"

migrate-upgrade: ## Применить миграции до последней версии в контейнере
	@echo "⬆️  Применение миграций..."
	@$(DOCKER_COMPOSE_CMD) exec bot alembic upgrade head

migrate-downgrade: ## Откатить миграции на одну версию назад в контейнере
	@echo "⬇️  Откат миграций..."
	@$(DOCKER_COMPOSE_CMD) exec bot alembic downgrade -1

migrate-history: ## Показать историю миграций
	@echo "📜 История миграций:"
	@$(DOCKER_COMPOSE_CMD) exec bot alembic history

migrate-current: ## Показать текущую версию миграций
	@echo "📍 Текущая версия:"
	@$(DOCKER_COMPOSE_CMD) exec bot alembic current

shell-bot: ## Открыть shell в контейнере бота
	@docker-compose exec bot /bin/bash

shell-rss: ## Открыть shell в контейнере RSS
	@docker-compose exec rss /bin/bash

shell-db: ## Открыть psql в контейнере БД
	@docker-compose exec db psql -U $$(docker-compose exec -T db printenv POSTGRES_USER) -d $$(docker-compose exec -T db printenv POSTGRES_DB)

restart-bot: ## Перезапустить только бота
	@echo "🔄 Перезапуск бота..."
	@docker-compose restart bot

restart-rss: ## Перезапустить только RSS
	@echo "🔄 Перезапуск RSS..."
	@docker-compose restart rss

restart-db: ## Перезапустить только БД
	@echo "🔄 Перезапуск БД..."
	@docker-compose restart db

pull: ## Обновить базовые образы
	@echo "📥 Обновление базовых образов..."
	@docker-compose pull

prune: ## Очистить неиспользуемые Docker ресурсы
	@echo "🧹 Очистка неиспользуемых ресурсов..."
	@docker system prune -f

stats: ## Показать статистику использования ресурсов
	@echo "📊 Статистика контейнеров:"
	@docker stats --no-stream $$(docker-compose ps -q)

