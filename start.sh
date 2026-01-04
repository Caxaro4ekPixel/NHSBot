#!/usr/bin/env bash

set -e

if [ -d ".venv" ] && [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
fi

echo "🚀 Starting RealES Bot deployment..."

if [ ! -f .env ]; then
    echo "⚠️  Warning: .env file not found. Creating from .env.example if exists..."
    if [ -f .env.example ]; then
        cp .env.example .env
        echo "✅ Created .env from .env.example"
    else
        echo "❌ Error: .env file is required. Please create it."
        exit 1
    fi
fi

if [ -f .env ]; then
    while IFS= read -r line || [ -n "$line" ]; do
        line=$(echo "$line" | tr -d '\r' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
        if [ -n "$line" ] && [[ ! "$line" =~ ^# ]]; then
            export "$line"
        fi
    done < .env
fi

DB_TYPE=${DB_TYPE:-postgres}

if [ "$DB_TYPE" != "postgres" ] && [ "$DB_TYPE" != "sqlite" ]; then
    echo "❌ Error: DB_TYPE must be either 'postgres' or 'sqlite'"
    exit 1
fi

DOCKER_COMPOSE_CMD="docker-compose"
if ! command -v docker-compose > /dev/null 2>&1; then
    if command -v docker > /dev/null 2>&1 && docker compose version > /dev/null 2>&1; then
        DOCKER_COMPOSE_CMD="docker compose"
    else
        echo "❌ Error: docker-compose or docker compose not found"
        exit 1
    fi
fi

if [ "$DB_TYPE" = "postgres" ]; then
    echo "📦 Starting PostgreSQL database container..."
    $DOCKER_COMPOSE_CMD up -d db
    
    echo "⏳ Waiting for database to be ready..."
    timeout=60
    counter=0
    while ! $DOCKER_COMPOSE_CMD exec -T db pg_isready -U ${DB_USER:-reales_bot} > /dev/null 2>&1; do
        sleep 1
        counter=$((counter + 1))
        if [ $counter -ge $timeout ]; then
            echo "❌ Error: Database failed to start within $timeout seconds"
            exit 1
        fi
    done
    echo "✅ Database is ready"
fi

echo "📦 Building Docker images..."
$DOCKER_COMPOSE_CMD build

echo "🤖 Starting bot container..."
$DOCKER_COMPOSE_CMD up -d bot

echo "📡 Starting RSS watcher container..."
$DOCKER_COMPOSE_CMD up -d rss

echo "✅ All services started!"
echo ""
echo "📊 Container status:"
$DOCKER_COMPOSE_CMD ps

echo ""
echo "📝 To view logs:"
echo "   $DOCKER_COMPOSE_CMD logs -f bot    # Bot logs"
echo "   $DOCKER_COMPOSE_CMD logs -f rss    # RSS logs"
echo "   $DOCKER_COMPOSE_CMD logs -f        # All logs"
echo ""
echo "🛑 To stop all services:"
echo "   $DOCKER_COMPOSE_CMD down"

