"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ComponentProps, ReactNode } from "react";
import { machineHref } from "@/utils/routes";

type NavLinkProps = Omit<ComponentProps<typeof Link>, "href"> & {
  to: string;
  activeClassName?: string;
  children: ReactNode;
};

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
  const mergedClass = [className, isActive ? activeClassName : null].filter(Boolean).join(" ");
  return (
    <Link href={href} className={mergedClass || undefined} {...rest}>
      {children}
    </Link>
  );
}
