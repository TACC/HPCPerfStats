"use client";

import { QueryProvider } from "@/providers/query-provider";

export default function AppProviders({ children }: { children: React.ReactNode }) {
  return <QueryProvider>{children}</QueryProvider>;
}
