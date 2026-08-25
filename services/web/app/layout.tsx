import "./globals.css";

// Root layout is intentionally bare: just <html>/<body>, the theme
// class script, and the font/token stylesheet. The Sidebar+Header app
// shell that used to live here now belongs to app/(app)/layout.tsx,
// because the public landing page (app/page.tsx) is not part of the
// cockpit and shouldn't be wrapped in it -- a visitor who hasn't
// launched the product yet should never see the Nodes/Alerts/Copilot
// sidebar. Route groups don't affect any existing URL, so /dashboard,
// /nodes, /copilot, etc. all still resolve exactly as before.
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

      <body className="min-h-screen bg-canvas text-color-text">{children}</body>
    </html>
  );
}