import "./globals.css";
import Link from "next/link";

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <script
          dangerouslySetInnerHTML={{
            __html: `
              try {
                const t = localStorage.getItem('cortex-theme');

                if (
                  t === 'dark' ||
                  (!t && matchMedia('(prefers-color-scheme: dark)').matches)
                ) {
                  document.documentElement.classList.add('dark');
                }
              } catch (e) {}
            `,
          }}
        />
      </head>

      <body className="bg-gray-50 text-gray-900 dark:bg-gray-950 dark:text-gray-100">
        <nav className="border-b bg-white px-6 py-3 flex gap-4 dark:bg-gray-900 dark:border-gray-700">
          <Link href="/dashboard" className="font-medium">
            Dashboard
          </Link>

          <Link href="/nodes" className="font-medium">
            Nodes
          </Link>
        </nav>

        {children}
      </body>
    </html>
  );
}