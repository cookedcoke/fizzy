import { useEffect, useState, type ReactNode } from "react";
import { cn } from "@/lib/utils";

/** The bar along the top: sticks while you scroll, and frosts over the page once there's something under it. */
export function TopBar({ right, tag }: { right?: ReactNode; tag?: ReactNode }) {
  const [under, setUnder] = useState(false);
  useEffect(() => {
    const on = () => setUnder(scrollY > 8);
    on(); addEventListener("scroll", on, { passive: true });
    return () => removeEventListener("scroll", on);
  }, []);
  return (
    <header className={cn("sticky top-0 z-40 transition-[background-color,box-shadow,backdrop-filter] duration-300",
      under && "bg-cream/75 shadow-[0_1px_0_var(--line)] backdrop-blur-xl backdrop-saturate-150")}>
      <div className="mx-auto flex max-w-[960px] items-center justify-between px-[18px] pt-[max(12px,env(safe-area-inset-top))] pb-3">
        <span className="flex items-center gap-2 font-display text-[17px] font-extrabold">
          <img src="/static/logo.svg" alt="" className="size-7" />My Cooked Coke</span>
        <span className="flex items-center gap-2">{tag}{right}</span>
      </div>
    </header>
  );
}
