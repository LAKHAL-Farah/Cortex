#!/usr/bin/env bash
# Managed by Cortex (ansible role container_metrics) -- do not edit by hand.
#
# Writes one Prometheus textfile with the state of every Docker container on
# this node, for node_exporter's textfile collector to serve alongside the
# normal host metrics. Cortex's Living Model (services/api container_health.py)
# turns these into :Container vertices and alerts.
#
#   cortex_container_up{container,state,health}   1 running / 0 anything else
#   cortex_container_restart_count{container}     Docker's RestartCount
#   cortex_containers_scrape_timestamp_seconds    when THIS script last succeeded
#
# The scrape timestamp is what lets Cortex tell "all containers healthy" from
# "this collector stopped running" -- stale data is treated as unknown, never
# as healthy. If Docker is absent or its daemon is down we write NOTHING (and
# leave the old file to go stale) rather than publish an empty, fresh-looking
# list that would make every container look removed.
set -u

OUT_DIR="${1:-/var/lib/node_exporter/textfile}"
OUT="${OUT_DIR}/cortex_containers.prom"

command -v docker >/dev/null 2>&1 || exit 0
docker info >/dev/null 2>&1 || exit 0

TMP="$(mktemp "${OUT_DIR}/.cortex_containers.XXXXXX")" || exit 1
trap 'rm -f "${TMP}"' EXIT

IDS="$(docker ps -aq 2>/dev/null)" || exit 0

{
  echo "# HELP cortex_container_up 1 if the container is running, 0 otherwise."
  echo "# TYPE cortex_container_up gauge"
  if [ -n "${IDS}" ]; then
    # shellcheck disable=SC2086
    docker inspect --format '{{.Name}} {{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' ${IDS} 2>/dev/null |
    while read -r name state health; do
      up=0; [ "${state}" = "running" ] && up=1
      echo "cortex_container_up{container=\"${name#/}\",state=\"${state}\",health=\"${health}\"} ${up}"
    done
  fi
  echo "# HELP cortex_container_restart_count Docker restart count of the container."
  echo "# TYPE cortex_container_restart_count gauge"
  if [ -n "${IDS}" ]; then
    # shellcheck disable=SC2086
    docker inspect --format '{{.Name}} {{.RestartCount}}' ${IDS} 2>/dev/null |
    while read -r name restarts; do
      echo "cortex_container_restart_count{container=\"${name#/}\"} ${restarts}"
    done
  fi
  echo "# HELP cortex_containers_scrape_timestamp_seconds Unix time of the last successful container scan."
  echo "# TYPE cortex_containers_scrape_timestamp_seconds gauge"
  echo "cortex_containers_scrape_timestamp_seconds $(date +%s)"
} > "${TMP}"

chmod 0644 "${TMP}"
mv -f "${TMP}" "${OUT}"
trap - EXIT
