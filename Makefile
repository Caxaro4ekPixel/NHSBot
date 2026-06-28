.PHONY: start stop logs backup prod-logs prod-logs-bot prod-logs-rss prod-ps prod-restart

PROD_CTX = --context nhsbot
PROD_DIR = /root/NHSBot

# ── Local ──────────────────────────────────────────────────────────────────────

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

# ── Production (remote) ────────────────────────────────────────────────────────

prod-ps:
	@docker $(PROD_CTX) ps --format "table {{.Names}}\t{{.Status}}\t{{.RunningFor}}"

prod-logs:
	@docker $(PROD_CTX) logs -f --tail=100 reales_bot

prod-logs-rss:
	@docker $(PROD_CTX) logs -f --tail=100 reales_bot_rss

prod-restart:
	@ssh root@78.40.209.76 "cd $(PROD_DIR) && docker compose restart bot rss"
	@echo "✅ Bot and RSS restarted"

prod-deploy:
	@ssh root@78.40.209.76 "cd $(PROD_DIR) && git pull && docker compose up --build -d bot rss"
	@echo "✅ Deployed"
