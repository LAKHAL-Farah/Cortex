import "./globals.css";
import Link from "next/link";
import Sidebar from "../components/Sidebar";
import Header from "../components/Header";

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

      <body className="min-h-screen bg-canvas text-color-text">
        <div className="grid min-h-screen grid-cols-[224px_1fr] gap-4 p-4">
          <Sidebar />
          <div className="min-h-screen">
            <div className="mx-auto max-w-[1600px]">
              <Header />
              <main className="mt-5">{children}</main>
            </div>
          </div>
        </div>
      </body>
    </html>
  );
}