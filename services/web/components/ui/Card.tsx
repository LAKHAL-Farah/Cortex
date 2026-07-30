import React from "react";

export function Card({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return (
    <div
      className={"bg-bg-sunk border border-[#ECECEC] p-6 " + className}
      style={{ borderRadius: 'var(--radius-card)', boxShadow: 'var(--shadow)' }}
    >
      {children}
    </div>
  );
}

export default Card;
