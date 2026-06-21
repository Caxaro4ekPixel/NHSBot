.PHONY: start stop logs backup

start:
	@chmod +x docker-entrypoint.sh
	@docker compose up --build -d
	@echo ""
	@docker compose ps

stop:
	@docker compose down

logs:
	@docker compose logs -f

backup:
	@mkdir -p backups
	@docker compose exec db pg_dump -U reales_bot reales_bot > backups/backup_$$(date +%Y%m%d_%H%M%S).sql
	@echo "✅ Backup saved to backups/"
	@ls -lh backups/ | tail -5
