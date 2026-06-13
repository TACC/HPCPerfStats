"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ComponentProps, ReactNode } from "react";
import { cn } from "@/lib/utils";
import { machineHref } from "@/utils/routes";

type NavLinkProps = Omit<ComponentProps<typeof Link>, "href"> & {
  to: string;
  activeClassName?: string;
  children: ReactNode;
};

const LEGACY_ACTIVE_CLASS = "active";
const DEFAULT_ACTIVE_TAILWIND = "font-semibold text-primary underline-offset-4";

function resolveActiveClassName(activeClassName?: string) {
  if (!activeClassName || activeClassName === LEGACY_ACTIVE_CLASS) {
    return DEFAULT_ACTIVE_TAILWIND;
  }
  return activeClassName;
}

export default function NavLink({
  to,
  className,
  activeClassName,
  children,
  ...rest
}: NavLinkProps) {
  const pathname = usePathname();
  const href = to.startsWith("/machine") || to.startsWith("/pub") ? to : machineHref(to);
  const normalizedHref = href.endsWith("/") ? href : `${href}/`;
  const normalizedPath = pathname?.endsWith("/") ? pathname : `${pathname}/`;
  const isActive =
    normalizedPath === normalizedHref ||
    (normalizedHref !== "/machine/" && normalizedPath.startsWith(normalizedHref));

  return (
    <Link
      href={href}
      className={cn(className, isActive && resolveActiveClassName(activeClassName))}
      aria-current={isActive ? "page" : undefined}
      {...rest}
    >
      {children}
    </Link>
  );
}
