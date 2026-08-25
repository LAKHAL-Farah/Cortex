import LandingPage from "@/components/landing/LandingPage";

// Public marketing route. Used to redirect straight to /dashboard -- now
// that /dashboard (and the rest of the cockpit) lives under app/(app) with
// its own Sidebar/Header shell, "/" is free to be an actual landing page.
// "Open the cockpit" on this page is the old redirect target, just as an
// explicit link instead of an automatic one.
export default function Home() {
  return <LandingPage />;
}
