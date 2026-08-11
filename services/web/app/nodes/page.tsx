"use client";

import NodeForm from "@/components/NodeForm";
import NodeTable from "@/components/NodeTable";

export default function NodesPage() {
  return (
    <main className="grid gap-4">
      <div className="panel flex items-center justify-between gap-3 p-5">
        <div>
          <div className="eyebrow">Infrastructure</div>
          <h1 className="font-display mt-1 text-lg font-semibold text-color-text">Nodes</h1>
        </div>
        <NodeForm />
      </div>
      <NodeTable />
    </main>
  );
}
