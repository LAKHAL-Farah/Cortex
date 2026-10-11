import { useEffect, useRef } from "react";
import { useReducedMotion } from "framer-motion";

/**
 * Artistic point-cloud / molecule field for the landing page only.
 *
 * Renders on a single canvas layered behind the page content:
 *  - a drifting field of "molecules" (a core dot with 2-4 satellite dots wired
 *    to it, echoing the Cortex logo mark)
 *  - a fine dust of points that thickens near the cursor
 *  - proximity links between nearby molecules, so the field reads as a graph
 *
 * Molecules ease toward the pointer when it comes close and settle back to
 * their drift when it leaves. Everything is drawn in the theme's palette,
 * sampled from CSS variables so light/dark both look right.
 */

type Satellite = { angle: number; dist: number; r: number; speed: number; hue: 0 | 1 | 2 | 3 };

type Molecule = {
  x: number;
  y: number;
  vx: number;
  vy: number;
  r: number;
  ox: number; // pointer offset, eased
  oy: number;
  spin: number;
  sats: Satellite[];
};

type Dust = { x: number; y: number; r: number; vx: number; vy: number; a: number };

const PALETTE_VARS = ["--primary", "--info", "--success", "--violet"] as const;

function readPalette(scope: Element = document.documentElement) {
  const style = getComputedStyle(scope);
  return PALETTE_VARS.map((v) => style.getPropertyValue(v).trim() || "0 0% 50%");
}

export function MoleculeField({ className = "" }: { className?: string }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const reduce = useReducedMotion();

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const paletteScope = canvas.closest("div") ?? document.documentElement;
    let palette = readPalette(paletteScope);
    const themeObserver = new MutationObserver(() => {
      palette = readPalette(paletteScope);
    });
    themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });

    let w = 0;
    let h = 0;
    let dpr = 1;
    let molecules: Molecule[] = [];
    let dust: Dust[] = [];

    const pointer = { x: -9999, y: -9999, active: false };

    const rand = (a: number, b: number) => a + Math.random() * (b - a);

    const build = () => {
      const area = w * h;
      const count = Math.round(Math.min(26, Math.max(9, area / 62000)));
      molecules = Array.from({ length: count }, () => {
        const satCount = Math.round(rand(2, 4));
        return {
          x: rand(0, w),
          y: rand(0, h),
          vx: rand(-0.09, 0.09),
          vy: rand(-0.07, 0.07),
          r: rand(2.1, 3.6),
          ox: 0,
          oy: 0,
          spin: rand(-0.0035, 0.0035),
          sats: Array.from({ length: satCount }, (_, i) => ({
            angle: rand(0, Math.PI * 2) + i,
            dist: rand(13, 30),
            r: rand(1.1, 2.1),
            speed: rand(-0.006, 0.006),
            hue: Math.floor(rand(0, 4)) as 0 | 1 | 2 | 3,
          })),
        };
      });

      const dustCount = Math.round(Math.min(220, Math.max(70, area / 8200)));
      dust = Array.from({ length: dustCount }, () => ({
        x: rand(0, w),
        y: rand(0, h),
        r: rand(0.5, 1.5),
        vx: rand(-0.05, 0.05),
        vy: rand(-0.05, 0.05),
        a: rand(0.12, 0.5),
      }));
    };

    const resize = () => {
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      w = canvas.clientWidth;
      h = canvas.clientHeight;
      canvas.width = Math.floor(w * dpr);
      canvas.height = Math.floor(h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      build();
    };

    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(canvas);

    const onMove = (e: PointerEvent) => {
      const rect = canvas.getBoundingClientRect();
      pointer.x = e.clientX - rect.left;
      pointer.y = e.clientY - rect.top;
      pointer.active = true;
    };
    const onLeave = () => {
      pointer.active = false;
      pointer.x = -9999;
      pointer.y = -9999;
    };
    window.addEventListener("pointermove", onMove, { passive: true });
    window.addEventListener("pointerleave", onLeave);

    const col = (i: number, alpha: number) => `hsl(${palette[i]} / ${alpha})`;

    let raf = 0;
    let last = 0;

    const draw = (t: number) => {
      raf = requestAnimationFrame(draw);
      if (t - last < 28) return; // ~35fps is plenty for a slow field
      last = t;

      ctx.clearRect(0, 0, w, h);

      // --- dust: fine point graphic, brighter near the cursor -------------
      for (const d of dust) {
        if (!reduce) {
          d.x += d.vx;
          d.y += d.vy;
          if (d.x < 0) d.x += w;
          if (d.x > w) d.x -= w;
          if (d.y < 0) d.y += h;
          if (d.y > h) d.y -= h;
        }
        const dx = d.x - pointer.x;
        const dy = d.y - pointer.y;
        const dist = Math.hypot(dx, dy);
        const near = dist < 190 ? 1 - dist / 190 : 0;
        ctx.beginPath();
        ctx.arc(d.x, d.y, d.r + near * 0.9, 0, Math.PI * 2);
        ctx.fillStyle = col(1, Math.min(0.85, d.a * 0.6 + near * 0.7));
        ctx.fill();
      }

      // --- molecules -------------------------------------------------------
      for (const m of molecules) {
        if (!reduce) {
          m.x += m.vx;
          m.y += m.vy;
          if (m.x < -40) m.x = w + 40;
          if (m.x > w + 40) m.x = -40;
          if (m.y < -40) m.y = h + 40;
          if (m.y > h + 40) m.y = -40;
        }

        // pointer attraction — only when the cursor comes close
        let tx = 0;
        let ty = 0;
        const pdx = pointer.x - m.x;
        const pdy = pointer.y - m.y;
        const pd = Math.hypot(pdx, pdy);
        if (pointer.active && pd < 220) {
          const pull = (1 - pd / 220) * 26;
          tx = (pdx / (pd || 1)) * pull;
          ty = (pdy / (pd || 1)) * pull;
        }
        m.ox += (tx - m.ox) * 0.06;
        m.oy += (ty - m.oy) * 0.06;

        const cx = m.x + m.ox;
        const cy = m.y + m.oy;
        const excite = Math.min(1, Math.hypot(m.ox, m.oy) / 22);

        // links to neighbours — the graph reading
        for (const n of molecules) {
          if (n === m) continue;
          const nx = n.x + n.ox;
          const ny = n.y + n.oy;
          const dd = Math.hypot(nx - cx, ny - cy);
          if (dd > 190) continue;
          ctx.beginPath();
          ctx.moveTo(cx, cy);
          ctx.lineTo(nx, ny);
          ctx.strokeStyle = col(1, (1 - dd / 190) * (0.1 + excite * 0.18));
          ctx.lineWidth = 0.7;
          ctx.stroke();
        }

        // bonds + satellites
        for (const s of m.sats) {
          if (!reduce) s.angle += s.speed + m.spin;
          const sx = cx + Math.cos(s.angle) * s.dist * (1 + excite * 0.16);
          const sy = cy + Math.sin(s.angle) * s.dist * (1 + excite * 0.16);

          ctx.beginPath();
          ctx.moveTo(cx, cy);
          ctx.lineTo(sx, sy);
          ctx.strokeStyle = col(s.hue, 0.2 + excite * 0.3);
          ctx.lineWidth = 0.9;
          ctx.stroke();

          ctx.beginPath();
          ctx.arc(sx, sy, s.r + excite * 0.5, 0, Math.PI * 2);
          ctx.fillStyle = col(s.hue, 0.5 + excite * 0.4);
          ctx.fill();
        }

        // core with a soft bloom — the "glow" of the field
        const glow = ctx.createRadialGradient(cx, cy, 0, cx, cy, m.r * (7 + excite * 6));
        glow.addColorStop(0, col(0, 0.22 + excite * 0.3));
        glow.addColorStop(1, col(0, 0));
        ctx.fillStyle = glow;
        ctx.beginPath();
        ctx.arc(cx, cy, m.r * (7 + excite * 6), 0, Math.PI * 2);
        ctx.fill();

        ctx.beginPath();
        ctx.arc(cx, cy, m.r + excite * 0.7, 0, Math.PI * 2);
        ctx.fillStyle = col(0, 0.75);
        ctx.fill();
      }
    };

    raf = requestAnimationFrame(draw);

    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      themeObserver.disconnect();
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerleave", onLeave);
    };
  }, [reduce]);

  return (
    <canvas
      ref={canvasRef}
      aria-hidden="true"
      className={`pointer-events-none ${className}`}
    />
  );
}
