import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 font-semibold transition-[transform,background-color,color,opacity] active:scale-[.97] disabled:opacity-60 disabled:pointer-events-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-red",
  {
    variants: {
      variant: {
        primary: "w-full rounded-full bg-red text-white text-[16px] font-display font-bold py-3.5 shadow-[0_10px_22px_-12px_rgba(179,0,0,.7)] hover:bg-red-solid",
        pill: "rounded-full border-[1.5px] border-ink bg-transparent text-ink text-[13.5px] px-3.5 py-1 hover:bg-ink hover:text-cream",
        ghost: "rounded-full text-muted hover:text-ink px-2 py-1 text-[13px]",
      },
    },
    defaultVariants: { variant: "primary" },
  },
);

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(({ className, variant, ...props }, ref) => (
  <button ref={ref} className={cn(buttonVariants({ variant }), className)} {...props} />
));
Button.displayName = "Button";
export { buttonVariants };
