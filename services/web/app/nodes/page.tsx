import NodeForm from "@/components/NodeForm";
import NodeTable from "@/components/NodeTable";

export default function NodesPage() {
  return (
    <main className="max-w-5xl mx-auto p-6 space-y-8">
      <h1 className="text-xl font-semibold">Nodes</h1>
      <NodeForm />
      <NodeTable />
    </main>
  );
}