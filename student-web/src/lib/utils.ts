import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** shadcn's class helper: 21st.dev components import it from "@/lib/utils". */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
