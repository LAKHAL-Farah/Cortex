"use client";

import React from "react";
import { usePathname } from "next/navigation";
import Sidebar from "./Sidebar";
import Header from "./Header";

// Routes that render their own full-bleed layout and must not be wrapped
// in the authenticated app chrome (sidebar nav + header with search/user
// badge) — showing that chrome on the login screen implied a session that
// didn't exist yet.
const CHROMELESS_ROUTES = ["/login", "/account/change-password"];

export default function AppChrome({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const chromeless = CHROMELESS_ROUTES.some((route) => pathname === route || pathname.startsWith(`${route}/`));

  if (chromeless) {
    return <>{children}</>;
  }

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
