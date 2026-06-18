"use client";

import { usePathname, useSearchParams } from "next/navigation";
import { useEffect } from "react";

type LayoutRouteChromeResetProps = {
  onReset: () => void;
};

/** Closes layout chrome overlays when pathname or query string changes (Suspense-safe useSearchParams). */
export default function LayoutRouteChromeReset({ onReset }: LayoutRouteChromeResetProps) {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const searchParamsKey = searchParams.toString();

  useEffect(() => {
    onReset();
  }, [pathname, searchParamsKey, onReset]);

  return null;
}
