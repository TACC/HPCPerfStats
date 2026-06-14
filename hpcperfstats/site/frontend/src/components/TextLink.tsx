import Link from "next/link";
import { cva, type VariantProps } from "class-variance-authority";
import type { ComponentPropsWithoutRef } from "react";

import { cn } from "@/lib/utils";

export const textLinkVariants = cva(
  "text-link underline underline-offset-4 decoration-link/50 hover:decoration-link focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
  {
    variants: {
      variant: {
        default: "",
        external: "",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  },
);

export function textLinkClassName(
  ...args: Parameters<typeof textLinkVariants>
): string {
  return textLinkVariants(...args);
}

type TextLinkProps = ComponentPropsWithoutRef<typeof Link> &
  VariantProps<typeof textLinkVariants>;

/** Inline navigation link with distinct color and at-rest underline. */
export function TextLink({
  className,
  variant,
  href,
  children,
  ...props
}: TextLinkProps) {
  return (
    <Link
      href={href}
      className={cn(textLinkVariants({ variant }), className)}
      {...props}
    >
      {children}
    </Link>
  );
}

type ExternalTextLinkProps = ComponentPropsWithoutRef<"a"> &
  VariantProps<typeof textLinkVariants>;

/** External inline link with safe defaults for off-site targets. */
export function ExternalTextLink({
  className,
  variant = "external",
  href,
  target = "_blank",
  rel = "noopener noreferrer",
  children,
  ...props
}: ExternalTextLinkProps) {
  return (
    <a
      href={href}
      target={target}
      rel={rel}
      className={cn(textLinkVariants({ variant }), className)}
      {...props}
    >
      {children}
    </a>
  );
}
