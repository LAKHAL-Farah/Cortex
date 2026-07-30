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

      <body className="min-h-screen bg-bg text-color-text">
        <div className="grid min-h-screen grid-cols-[250px_1fr] gap-6 px-6 py-6 lg:px-8 lg:py-8">
          <Sidebar />
          <div className="min-h-screen">
            <div className="mx-auto max-w-[1600px]">
              <Header />
              <main className="mt-6">{children}</main>
            </div>
          </div>
        </div>
      </body>
    </html>
  );
}