interface LogoProps {
  size?: "sm" | "md" | "lg";
  className?: string;
}

const sizes = {
  sm: { glyph: "w-7 h-7", text: "text-lg" },
  md: { glyph: "w-8 h-8", text: "text-[22px]" },
  lg: { glyph: "w-11 h-11", text: "text-2xl" },
};

/** Cortex mark — a small topology graph: three linked nodes around a core. */
function CortexGlyph({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} aria-hidden="true" fill="none">
      <rect x="1" y="1" width="30" height="30" rx="7" fill="var(--logo-glyph-bg, hsl(var(--slab)))" />
      <g stroke="hsl(var(--primary))" strokeWidth="1.4" strokeLinecap="round" opacity="0.85">
        <path d="M16 16 L9 9" />
        <path d="M16 16 L23.5 11" />
        <path d="M16 16 L12 24" />
      </g>
      <circle cx="16" cy="16" r="3.1" fill="hsl(var(--primary))" />
      <circle cx="9" cy="9" r="2" fill="hsl(var(--info))" />
      <circle cx="23.5" cy="11" r="2" fill="hsl(var(--success))" />
      <circle cx="12" cy="24" r="2" fill="hsl(var(--violet))" />
    </svg>
  );
}

export function Logo({ size = "md", className = "" }: LogoProps) {
  const s = sizes[size];
  return (
    <span className={`inline-flex items-center gap-2 ${className}`}>
      <CortexGlyph className={s.glyph} />
      <span className={`font-display font-bold text-foreground tracking-tight ${s.text}`}>
        Cortex
      </span>
    </span>
  );
}
