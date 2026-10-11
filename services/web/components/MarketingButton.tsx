"use client";

import React from "react";

type MarketingButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  asChild?: boolean;
  variant?: "default" | "outline" | "ghost";
  size?: "default" | "lg";
};

export function MarketingButton({
  asChild = false,
  variant = "default",
  size = "default",
  className = "",
  children,
  ...props
}: MarketingButtonProps) {
  const classes = [
    "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-full text-sm font-medium transition-[transform,colors,box-shadow] duration-200 ease-out hover:-translate-y-[1px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:pointer-events-none disabled:opacity-50",
    size === "lg" ? "h-12 px-8" : "h-11 px-4",
    variant === "outline" ? "border border-input bg-background hover:bg-muted" : "",
    variant === "ghost" ? "hover:bg-accent hover:text-accent-foreground" : "",
    variant === "default" ? "bg-foreground text-background hover:bg-foreground/90" : "",
    className,
  ].filter(Boolean).join(" ");

  if (asChild && React.isValidElement<{ className?: string }>(children)) {
    return React.cloneElement(children, {
      className: `${classes} ${children.props.className ?? ""}`,
    });
  }

  return (
    <button className={classes} {...props}>
      {children}
    </button>
  );
}
