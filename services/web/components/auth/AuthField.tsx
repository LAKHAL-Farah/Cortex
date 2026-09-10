"use client";

import React from "react";
import { Eye, EyeOff } from "lucide-react";
import type { LucideIcon } from "lucide-react";

export function AuthField({
  id,
  label,
  type = "text",
  icon: Icon,
  showToggle = false,
  value,
  onChange,
  autoFocus,
  required,
  minLength,
}: {
  id: string;
  label: string;
  type?: string;
  icon?: LucideIcon;
  showToggle?: boolean;
  value: string;
  onChange: (value: string) => void;
  autoFocus?: boolean;
  required?: boolean;
  minLength?: number;
}) {
  const [visible, setVisible] = React.useState(false);
  const inputType = showToggle ? (visible ? "text" : "password") : type;
  const leftPad = Icon ? "pl-9" : "pl-3";
  const rightPad = showToggle ? "pr-9" : "pr-3";

  return (
    <label htmlFor={id} className="flex flex-col gap-1 text-sm text-text-dim">
      {label}
      <div className="relative">
        {Icon && (
          <Icon
            className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted"
            strokeWidth={1.75}
          />
        )}
        <input
          id={id}
          name={id}
          type={inputType}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          autoFocus={autoFocus}
          required={required}
          minLength={minLength}
          className={`w-full rounded-[var(--radius-control)] py-2.5 text-sm text-color-text outline-none transition-colors ${leftPad} ${rightPad}`}
          style={{ border: "1px solid var(--border)", background: "var(--canvas)" }}
          onFocus={(e) => (e.currentTarget.style.borderColor = "var(--accent)")}
          onBlur={(e) => (e.currentTarget.style.borderColor = "var(--border)")}
        />
        {showToggle && (
          <button
            type="button"
            tabIndex={-1}
            onClick={() => setVisible((v) => !v)}
            aria-label={visible ? "Hide password" : "Show password"}
            className="absolute right-2.5 top-1/2 -translate-y-1/2 text-text-muted transition-colors hover:text-text-dim"
          >
            {visible ? <EyeOff className="h-4 w-4" strokeWidth={1.75} /> : <Eye className="h-4 w-4" strokeWidth={1.75} />}
          </button>
        )}
      </div>
    </label>
  );
}

export default AuthField;
