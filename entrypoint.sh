#!/usr/bin/env bash
set -Eeuo pipefail

DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-3306}"
DB_NAME="${DB_NAME:-blog}"
DB_TABLES_CREATE_MANUALLY="${DB_TABLES_CREATE_MANUALLY:-false}"
AUTO_START_WEB="${AUTO_START_WEB:-false}"
STARTUP_TIMEOUT_SECONDS="${STARTUP_TIMEOUT_SECONDS:-180}"
HEALTHCHECK_INTERVAL_SECONDS="${HEALTHCHECK_INTERVAL_SECONDS:-30}"
HEALTHCHECK_FAILURE_THRESHOLD="${HEALTHCHECK_FAILURE_THRESHOLD:-3}"
ADMIN_HEALTHCHECK_URL="${ADMIN_HEALTHCHECK_URL:-http://127.0.0.1:9090/}"
WEB_HEALTHCHECK_URL="${WEB_HEALTHCHECK_URL:-http://127.0.0.1:8080/}"

for variable in DB_USERNAME DB_PASSWORD; do
    if [[ -z "${!variable:-}" ]]; then
        echo "Error: $variable environment variable is required" >&2
        exit 1
    fi
done
for variable in STARTUP_TIMEOUT_SECONDS HEALTHCHECK_INTERVAL_SECONDS HEALTHCHECK_FAILURE_THRESHOLD; do
    if [[ ! "${!variable}" =~ ^[1-9][0-9]*$ ]]; then
        echo "Error: $variable must be a positive integer" >&2
        exit 1
    fi
done
if [[ "$AUTO_START_WEB" != true && "$AUTO_START_WEB" != false ]]; then
    echo 'Error: AUTO_START_WEB must be true or false' >&2
    exit 1
fi

# Protect the sed replacement delimiter, ampersands and literal backslashes.
escape_replacement() { printf '%s' "$1" | sed 's/[\\&|]/\\&/g'; }
SETUP_FILE=settings/setup.properties
if [[ -f "$SETUP_FILE" ]]; then
    JDBC_URL="jdbc:mysql://${DB_HOST}:${DB_PORT}/${DB_NAME}"
    echo "Configuring database settings in $SETUP_FILE..."
    sed -i \
        -e "s|^datasource\.jdbc_url=.*|datasource.jdbc_url=$(escape_replacement "$JDBC_URL")|" \
        -e "s|^datasource\.username=.*|datasource.username=$(escape_replacement "$DB_USERNAME")|" \
        -e "s|^datasource\.password=.*|datasource.password=$(escape_replacement "$DB_PASSWORD")|" \
        -e "s|^tables\.create_manually=.*|tables.create_manually=$(escape_replacement "$DB_TABLES_CREATE_MANUALLY")|" \
        "$SETUP_FILE"
else
    echo "Error: $SETUP_FILE not found" >&2
    exit 1
fi

mkdir -p logs
lanes=(admin)
if [[ "$AUTO_START_WEB" == true ]]; then lanes+=(web); fi
log_files=()
for lane in "${lanes[@]}"; do
    script="$lane/start-service.sh"
    # CLI launches in the background and historically discards fatal JVM exceptions.
    # Keep its generated classpath/VM options, but retain stdout/stderr for diagnostics.
    if grep -q '> /dev/null 2>&1 &' "$script"; then
        sed -i "s@> /dev/null 2>&1 &@>> logs/$lane-console.log 2>\&1 \&@" "$script"
    elif ! grep -q ">> logs/$lane-console.log 2>&1 &" "$script"; then
        echo "Error: unsupported launch script $script; refusing to discard diagnostics" >&2
        exit 1
    fi
    rm -f "$lane/.runtime/${lane}_server_instance.pid"
    touch "logs/$lane-server.log" "logs/$lane-console.log"
    log_files+=("logs/$lane-server.log" "logs/$lane-console.log")
done
# Remove only known stale PID files; never wipe the persistent platform runtime cache.
rm -f socket/.runtime/socket_server_instance.pid

tail -n 20 -F "${log_files[@]}" &
tail_pid=$!
shutdown() {
    trap - EXIT INT TERM
    kill "$tail_pid" 2>/dev/null || true
    for lane in "${lanes[@]}"; do
        pid_file="$lane/.runtime/${lane}_server_instance.pid"
        [[ -f "$pid_file" ]] || continue
        read -r pid < "$pid_file" || true
        if [[ "${pid:-}" =~ ^[0-9]+$ && -r "/proc/$pid/cmdline" ]]; then
            command_line=$(tr '\0' ' ' < "/proc/$pid/cmdline")
            if [[ "$command_line" == *"org.youngmonkeys.ezyplatform.$lane.${lane^}Startup"* ]]; then
                kill -TERM "$pid" 2>/dev/null || true
            fi
        fi
    done
}
trap shutdown EXIT
trap 'exit 143' TERM
trap 'exit 130' INT

for lane in "${lanes[@]}"; do bash cli.sh "start $lane"; done

healthy() {
    local status
    status=$(curl -sS -o /dev/null -w '%{http_code}' --connect-timeout 2 --max-time 5 "$1" 2>/dev/null) || return 1
    [[ "$status" =~ ^[23][0-9][0-9]$ ]]
}
all_healthy() {
    healthy "$ADMIN_HEALTHCHECK_URL" || return 1
    [[ "$AUTO_START_WEB" != true ]] || healthy "$WEB_HEALTHCHECK_URL"
}

# A live PID can be a failed JVM kept alive by non-daemon workers. Prove HTTP readiness.
deadline=$((SECONDS + STARTUP_TIMEOUT_SECONDS))
until all_healthy; do
    if (( SECONDS >= deadline )); then
        echo "Error: HTTP startup timed out after ${STARTUP_TIMEOUT_SECONDS}s; exiting for the orchestrator to recover" >&2
        for lane in "${lanes[@]}"; do tail -n 80 "logs/$lane-console.log" >&2; done
        exit 1
    fi
    sleep 2
done
echo 'HTTP readiness confirmed for all enabled services'

# Let Docker/Swarm own restarts. Do not run a cron loop that only calls `start web`
# against an already-live but failed JVM, or races a second shared-volume writer.
failures=0
while true; do
    sleep "$HEALTHCHECK_INTERVAL_SECONDS"
    if all_healthy; then
        failures=0
    else
        failures=$((failures + 1))
        echo "HTTP health check failed ($failures/$HEALTHCHECK_FAILURE_THRESHOLD)" >&2
        if (( failures >= HEALTHCHECK_FAILURE_THRESHOLD )); then
            echo 'Error: unhealthy service; exiting for the orchestrator to recover' >&2
            exit 1
        fi
    fi
done
