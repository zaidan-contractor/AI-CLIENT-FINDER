#!/usr/bin/env bash
set -e

service postgresql start
service redis-server start

# Configure postgres to listen on all interfaces
PG_CONF=$(find /etc/postgresql/ -name "postgresql.conf" | head -n 1)
PG_HBA=$(find /etc/postgresql/ -name "pg_hba.conf" | head -n 1)

echo "listen_addresses = '*'" >> "$PG_CONF"
echo "host all all 0.0.0.0/0 md5" >> "$PG_HBA"
echo "host all all ::/0 md5" >> "$PG_HBA"

service postgresql restart

# Create user and database
su - postgres -c "psql -tc \"SELECT 1 FROM pg_user WHERE usename = 'platform_user'\" | grep -q 1 || psql -c \"CREATE USER platform_user WITH PASSWORD 'platform_password_123' SUPERUSER;\""
su - postgres -c "psql -tc \"SELECT 1 FROM pg_database WHERE datname = 'opportunity_platform'\" | grep -q 1 || psql -c \"CREATE DATABASE opportunity_platform OWNER platform_user;\""

echo "PostgreSQL and Redis setup complete!"
