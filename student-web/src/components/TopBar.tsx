import type { ReactNode } from "react";

export function TopBar({ right }: { right?: ReactNode }) {
  return (
    <div className="mx-auto flex max-w-[960px] items-center justify-between px-[18px] pt-3.5 pt-[max(14px,env(safe-area-inset-top))]">
      <span className="flex items-center gap-2 font-display text-[17px] font-extrabold">
        <img src="/static/logo.svg" alt="" className="size-7" />My Cooked Coke</span>
      {right}
    </div>
  );
}
