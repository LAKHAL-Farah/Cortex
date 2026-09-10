import "./globals.css";
import Link from "next/link";
import AppChrome from "../components/AppChrome";

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
        <AppChrome>{children}</AppChrome>
      </body>
    </html>
  );
}