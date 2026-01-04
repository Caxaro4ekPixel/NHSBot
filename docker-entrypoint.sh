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

export PGHOST=${DB_HOST:-db}
export PGPORT=${DB_PORT:-5432}
export PGDATABASE=${DB_NAME:-reales_bot}
export PGUSER=${DB_USER:-reales_bot}
export PGPASSWORD=${DB_PASSWORD:-reales_bot}

CURRENT_OUTPUT=$(alembic current 2>&1 || true)

if echo "$CURRENT_OUTPUT" | grep -qE "(Can't locate revision|Target database is not up to date)" || ! echo "$CURRENT_OUTPUT" | grep -q "rev:"; then
    if echo "$CURRENT_OUTPUT" | grep -q "Can't locate revision"; then
        echo "⚠️  Database contains invalid migration revision. Cleaning migration state..."
        if [ "$DB_TYPE" = "postgres" ]; then
            PGPASSWORD="${DB_PASSWORD:-reales_bot}" psql -h "${DB_HOST:-db}" -p "${DB_PORT:-5432}" -U "${DB_USER:-reales_bot}" -d "${DB_NAME:-reales_bot}" -c "DROP TABLE IF EXISTS alembic_version;" 2>&1 | grep -vE "(DROP TABLE|NOTICE)" || true
        fi
    fi
    echo "ℹ️  Stamping database with head revision..."
    alembic stamp head 2>&1 | grep -vE "(INFO|WARNING)" || true
    echo "✅ Database stamped with current schema"
else
    echo "⬆️  Upgrading database..."
    UPGRADE_OUTPUT=$(alembic upgrade head 2>&1)
    UPGRADE_EXIT=$?
    
    if [ $UPGRADE_EXIT -ne 0 ]; then
        if echo "$UPGRADE_OUTPUT" | grep -qE "(Can't locate revision|Target database is not up to date)"; then
            echo "⚠️  Migration chain is broken. Stamping with head revision..."
            alembic stamp head 2>&1 | grep -vE "(INFO|WARNING)" || true
            echo "✅ Database stamped with current schema"
        else
            echo "❌ Migration failed:"
            echo "$UPGRADE_OUTPUT" | grep -v "INFO"
            exit 1
        fi
    else
        echo "$UPGRADE_OUTPUT" | grep -v "INFO" || true
        echo "✅ Migrations applied successfully"
    fi
fi

echo "🚀 Starting application..."
exec "$@"
