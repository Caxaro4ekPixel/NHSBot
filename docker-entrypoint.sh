#!/bin/bash
set -e

echo "🔄 Waiting for database to be ready..."

if [ "$DB_TYPE" = "postgres" ]; then
    DB_HOST=${DB_HOST:-db}
    DB_PORT=${DB_PORT:-5432}
    DB_NAME=${DB_NAME:-reales_bot}
    DB_USER=${DB_USER:-reales_bot}

    timeout=60
    counter=0
    while ! PGPASSWORD="${DB_PASSWORD:-reales_bot}" pg_isready -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" > /dev/null 2>&1; do
        sleep 1
        counter=$((counter + 1))
        if [ $counter -ge $timeout ]; then
            echo "❌ Error: Database failed to start within $timeout seconds"
            exit 1
        fi
    done
    echo "✅ Database is ready"
fi

echo "⬆️  Running database migrations..."

_psql() {
    PGPASSWORD="${DB_PASSWORD:-reales_bot}" psql \
        -h "${DB_HOST:-db}" -p "${DB_PORT:-5432}" \
        -U "${DB_USER:-reales_bot}" -d "${DB_NAME:-reales_bot}" \
        -t -A -c "$1" 2>/dev/null
}

if [ "$DB_TYPE" = "postgres" ]; then
    HAS_VERSION_TABLE=$(_psql "SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='alembic_version';" || true)
    HAS_APP_TABLES=$(_psql "SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='releases';" || true)

    if [ "$HAS_VERSION_TABLE" = "1" ] && [ "$HAS_APP_TABLES" != "1" ]; then
        # Stale stamp: alembic thinks it's at head but tables are missing — reset and recreate
        echo "⚠️  Stale migration stamp detected (alembic_version exists but tables are missing). Resetting..."
        _psql "DROP TABLE alembic_version;" > /dev/null || true
        HAS_VERSION_TABLE=""
    fi

    if [ "$HAS_VERSION_TABLE" = "1" ]; then
        # Normal: migration state tracked and tables exist — apply any pending migrations
        alembic upgrade head 2>&1 | grep -v "^INFO" || true
        echo "✅ Migrations applied"
    elif [ "$HAS_APP_TABLES" = "1" ]; then
        # Tables exist without migration tracking (pre-Alembic DB) — stamp only
        echo "ℹ️  Existing database without migration tracking. Stamping..."
        alembic stamp head 2>&1 | grep -v "^INFO" || true
        echo "✅ Database stamped"
    else
        # Fresh empty database — create schema from scratch
        echo "ℹ️  Fresh database. Creating schema..."
        alembic upgrade head 2>&1 | grep -v "^INFO" || true
        echo "✅ Schema created"
    fi
else
    alembic upgrade head 2>&1 | grep -v "^INFO" || true
    echo "✅ Migrations applied"
fi

echo "🚀 Starting application..."
exec "$@"
