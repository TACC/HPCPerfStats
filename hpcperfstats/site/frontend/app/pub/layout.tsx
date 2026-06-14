"use client";

import { applyBokehResizeObserverDeferral } from "@/patch-resize-observer-for-bokeh";
import LayoutPub from "@/components/LayoutPub";

applyBokehResizeObserverDeferral();

/** Pub chrome only — dashboard data loads in page views (not layout). */
export default function PubLayout({ children }: { children: React.ReactNode }) {
  return <LayoutPub>{children}</LayoutPub>;
}
