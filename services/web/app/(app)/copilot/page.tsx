import CopilotChat from "@/components/CopilotChat";

export default function CopilotPage() {
  return (
    <main className="grid gap-4">
      <div>
        <div className="eyebrow">AI Copilot</div>
        <h1 className="font-display mt-1 text-lg font-semibold text-color-text">Chat</h1>
      </div>
      <CopilotChat />
    </main>
  );
}
