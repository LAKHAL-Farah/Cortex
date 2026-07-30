"use client";
import useSWR, { mutate } from "swr";
import type { Node } from "@/lib/types";

const fetcher = (url: string) => fetch(url).then((r) => r.json());

export default function NodeTable() {
  const { data: nodes, isLoading } = useSWR<Node[]>("/api/nodes", fetcher, {
    refreshInterval: 5000,   // covers the "install still pending" case without manual refresh
  });

  async function onDelete(id: string) {
    if (!confirm("Delete this node?")) return;
    const res = await fetch(`/api/nodes/${id}`, { method: "DELETE" });
    if (res.status === 204) {
      mutate("/api/nodes");
    } else {
      alert(`Delete failed: ${res.status}`);
    }
  }

  if (isLoading) return <p>Loading…</p>;
  if (!nodes?.length) return <p className="text-sm text-gray-500">No nodes registered yet.</p>;

  return (
    <table className="w-full text-sm border-collapse">
      <thead>
        <tr className="text-left border-b">
          <th className="py-2">Hostname</th>
          <th>IP</th>
          <th>Role</th>
          <th>Port</th>
          <th>Active</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        {nodes.map((n) => (
          <tr key={n.id} className="border-b">
            <td className="py-2">{n.hostname}</td>
            <td>{n.ip_address}</td>
            <td>{n.role}</td>
            <td>{n.exporter_port}</td>
            <td>{n.is_active ? "yes" : "no"}</td>
            <td>
              <button onClick={() => onDelete(n.id)} className="text-red-600 hover:underline">
                Delete
              </button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}