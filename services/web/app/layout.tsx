import "./globals.css";
import Link from "next/link";

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="bg-gray-50 text-gray-900">
        <nav className="border-b bg-white px-6 py-3 flex gap-4">
          <Link href="/dashboard" className="font-medium">Dashboard</Link>
          <Link href="/nodes" className="font-medium">Nodes</Link>
        </nav>
        {children}
      </body>
    </html>
  );
}
