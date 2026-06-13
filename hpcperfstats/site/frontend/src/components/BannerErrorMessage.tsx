import type { CSSProperties, ReactNode } from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { cn } from "@/lib/utils";

export type BannerErrorMessageProps = {
  message: ReactNode;
  className?: string;
  style?: CSSProperties;
  variant?: "page" | "inline";
};

/** Page-level `Error: …` banner, or `variant="inline"` for section errors. */
export default function BannerErrorMessage({
  message,
  className,
  style,
  variant = "page",
}: BannerErrorMessageProps) {
  if (variant === "inline") {
    return (
      <Alert
        variant="destructive"
        className={cn("border-none bg-transparent p-0 shadow-none", className)}
        style={style}
      >
        <AlertDescription className="text-destructive">{message}</AlertDescription>
      </Alert>
    );
  }
  return (
    <Alert
      variant="destructive"
      className={cn("mx-auto w-full max-w-7xl px-4", className)}
      style={style}
    >
      <AlertDescription className="text-destructive">Error: {message}</AlertDescription>
    </Alert>
  );
}
