import React from "react";

export function Card({
  children,
  className = "",
  interactive = false,
  padding = "p-5",
}: {
  children: React.ReactNode;
  className?: string;
  interactive?: boolean;
  padding?: string;
}) {
  return (
    <div className={`panel ${interactive ? "panel-interactive" : ""} ${padding} ${className}`}>
      {children}
    </div>
  );
}

export default Card;
