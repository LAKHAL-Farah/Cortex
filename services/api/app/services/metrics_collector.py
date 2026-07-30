from app.services.prometheus_client import query, query_range
import logging

logger = logging.getLogger(__name__)

import time

METRIC_QUERIES = {
    "cpu_percent":    '100 - (avg by(instance) (rate(node_cpu_seconds_total{{mode="idle"}}[5m])) * 100)',
    "memory_percent": '100 * (1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)',
    "disk_percent":   '100 * (1 - node_filesystem_avail_bytes{{mountpoint="/"}} / node_filesystem_size_bytes{{mountpoint="/"}})',
    "network_rx":     'sum by(instance) (rate(node_network_receive_bytes_total{{device!="lo"}}[5m]))',
    "network_tx":     'sum by(instance) (rate(node_network_transmit_bytes_total{{device!="lo"}}[5m]))',
    "load1":          'node_load1',
}


# ---------- Helpers ----------

def to_dict(results, label="instance"):
    return {
        item["metric"][label]: round(float(item["value"][1]), 2)
        for item in results
    }


def to_dict_with_meta(results):
    """Garde la valeur + le nom du node + son rôle (controller/storage/compute)."""
    data = {}
    for item in results:
        instance = item["metric"]["instance"]
        data[instance] = {
            "value": round(float(item["value"][1]), 2),
            "node": item["metric"].get("node", instance),
            "role": item["metric"].get("role", "unknown"),
        }
    return data


def human_bytes(n):
    """Convertit des octets/sec en unité lisible (KB/s, MB/s, ...)."""
    for unit in ["B", "KB", "MB", "GB"]:
        if n < 1024:
            return f"{n:.2f} {unit}/s"
        n /= 1024
    return f"{n:.2f} TB/s"


def human_uptime(seconds):
    """Convertit des secondes en 'Xj Yh'."""
    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    return f"{days}j {hours}h"


def health_status(cpu, memory, disk):
    """Détermine un statut de santé global à partir des 3 métriques clés."""
    if cpu > 90 or memory > 90 or disk > 90:
        return "critical"
    if cpu > 70 or memory > 70 or disk > 70:
        return "warning"
    return "healthy"


# ---------- CPU ----------

def get_cpu():
    promql = '100 - (avg by(instance) (rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)'
    return to_dict(query(promql))


# ---------- Memory ----------

def get_memory():
    promql = '100 * (1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)'
    return to_dict(query(promql))


def get_swap():
    promql = '''
    100 * (
        1 - (node_memory_SwapFree_bytes / node_memory_SwapTotal_bytes)
    ) and node_memory_SwapTotal_bytes > 0
    '''
    return to_dict(query(promql))


# ---------- Disk usage (partition racine uniquement) ----------

def get_disk():
    promql = '100 * (1 - node_filesystem_avail_bytes{mountpoint="/"} / node_filesystem_size_bytes{mountpoint="/"})'
    return to_dict(query(promql))


# ---------- Disk I/O ----------

def get_disk_read():
    promql = 'sum by(instance) (rate(node_disk_read_bytes_total[5m]))'
    return to_dict(query(promql))


def get_disk_write():
    promql = 'sum by(instance) (rate(node_disk_written_bytes_total[5m]))'
    return to_dict(query(promql))


# ---------- Réseau ----------

def get_network_rx():
    promql = 'sum by(instance) (rate(node_network_receive_bytes_total{device!="lo"}[5m]))'
    return to_dict(query(promql))


def get_network_tx():
    promql = 'sum by(instance) (rate(node_network_transmit_bytes_total{device!="lo"}[5m]))'
    return to_dict(query(promql))


# ---------- Load average ----------

def get_load1():
    return to_dict(query("node_load1"))


def get_load5():
    return to_dict(query("node_load5"))


def get_load15():
    return to_dict(query("node_load15"))


# ---------- Uptime ----------

def get_uptime():
    promql = "time() - node_boot_time_seconds"
    return to_dict(query(promql))


# ---------- Statut du node ----------

def get_status():
    promql = 'up{job="node_exporter"}'
    return to_dict(query(promql))


# ---------- Processus ----------

def get_procs_running():
    return to_dict(query("node_procs_running"))


def get_procs_blocked():
    return to_dict(query("node_procs_blocked"))


# ---------- Agrégation finale (utilisée par le dashboard) ----------

def collect_metrics():

    cpu = get_cpu()
    memory = get_memory()
    swap = get_swap()
    disk = get_disk()
    disk_read = get_disk_read()
    disk_write = get_disk_write()
    net_rx = get_network_rx()
    net_tx = get_network_tx()
    load1 = get_load1()
    load5 = get_load5()
    load15 = get_load15()
    uptime = get_uptime()
    status = get_status()
    procs_running = get_procs_running()
    procs_blocked = get_procs_blocked()

    # Récupère les métadonnées (node/role) à partir de n'importe quelle requête labellisée
    meta_source = query('up{job="node_exporter"}')
    meta = {
        item["metric"]["instance"]: {
            "node": item["metric"].get("node", item["metric"]["instance"]),
            "role": item["metric"].get("role", "unknown"),
        }
        for item in meta_source
    }

    all_instances = set(cpu) | set(memory) | set(disk) | set(status)

    nodes = []

    for instance in all_instances:

        cpu_val = cpu.get(instance, 0)
        mem_val = memory.get(instance, 0)
        disk_val = disk.get(instance, 0)

        nodes.append({
            "node": meta.get(instance, {}).get("node", instance),
            "role": meta.get(instance, {}).get("role", "unknown"),
            "instance": instance,

            "cpu_percent": cpu_val,
            "memory_percent": mem_val,
            "swap_percent": swap.get(instance, 0),
            "disk_percent": disk_val,

            "disk_read": human_bytes(disk_read.get(instance, 0)),
            "disk_write": human_bytes(disk_write.get(instance, 0)),
            "network_rx": human_bytes(net_rx.get(instance, 0)),
            "network_tx": human_bytes(net_tx.get(instance, 0)),

            "load1": load1.get(instance, 0),
            "load5": load5.get(instance, 0),
            "load15": load15.get(instance, 0),

            "uptime": human_uptime(uptime.get(instance, 0)),

            "status": "up" if status.get(instance, 0) == 1 else "down",
            "health": health_status(cpu_val, mem_val, disk_val),

            "procs_running": procs_running.get(instance, 0),
            "procs_blocked": procs_blocked.get(instance, 0),
        })

    return nodes




def get_history(instance: str, minutes: int = 60, step: str = "15s"):
    end = time.time()
    start = end - minutes * 60
    out = {}
    for name, promql in METRIC_QUERIES.items():
        scoped = f'({promql}){{instance="{instance}"}}' if "{{instance" not in promql else promql
        try:
            results = query_range(scoped, start, end, step)
            series = []
            if results:
                if len(results) == 1:
                    series = results[0].get("values", [])
                else:
                    for result in results:
                        if result.get("metric", {}).get("instance") == instance:
                            series = result.get("values", [])
                            break
                    if not series:
                        series = results[0].get("values", [])
        except Exception:
            logger.exception("failed to fetch history for %s (metric=%s)", instance, name)
            series = []

        out[name] = [{"t": int(float(ts)), "v": round(float(v), 2)} for ts, v in series]
    return out