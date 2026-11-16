import * as React from "react";
import { cn } from "@/lib/utils";

export function Card({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("rounded-[18px] bg-card px-[18px] py-4 shadow-card", className)} {...props} />;
}

export function Section({ title, small, children }: { title: React.ReactNode; small?: React.ReactNode; children?: React.ReactNode }) {
  return (
    <section>
      <h2 className="mt-[30px] mb-2.5 flex items-baseline gap-2 text-[19px] font-[750]">
        {title}{small != null && <small className="font-sans text-[13px] font-normal text-muted">{small}</small>}
      </h2>
      {children}
    </section>
  );
}

/** A thin progress bar that grows into place. */
export function Bar({ pct, className }: { pct: number; className?: string }) {
  return (
    <div className={cn("mt-2 h-2 overflow-hidden rounded-full bg-line", className)}>
      <div className="h-full rounded-full bg-gradient-to-r from-[#FF5A3C] to-red transition-[width] duration-700 ease-out"
        style={{ width: `${Math.max(0, Math.min(100, pct))}%` }} />
    </div>
  );
}
