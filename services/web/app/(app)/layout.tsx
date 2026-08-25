import Sidebar from "../../components/Sidebar";
import Header from "../../components/Header";

// Everything under this group is the authenticated cockpit (dashboard,
// nodes, topology, copilot, ...) and keeps the Sidebar/Header shell that
// used to live in the root layout. The public landing page at app/page.tsx
// sits outside this group so it isn't forced into the app chrome -- see
// that file's comment for why the split happened.
export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="grid min-h-screen grid-cols-[224px_1fr] gap-4 p-4">
      <Sidebar />
      <div className="min-h-screen">
        <div className="mx-auto max-w-[1600px]">
          <Header />
          <main className="mt-5">{children}</main>
        </div>
      </div>
    </div>
  );
}
