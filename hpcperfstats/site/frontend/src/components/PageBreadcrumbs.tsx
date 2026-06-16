import Link from "next/link";
import { Fragment } from "react";
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb";
import { cn } from "@/lib/utils";
import { machineHref } from "@/utils/routes";

function breadcrumbHref(to: string): string {
  if (to.startsWith("/machine") || to.startsWith("/pub")) {
    return to;
  }
  return machineHref(to);
}

export type BreadcrumbItem = {
  label: string;
  to?: string;
};

export type PageBreadcrumbsProps = {
  items: BreadcrumbItem[];
};

export default function PageBreadcrumbs({ items }: PageBreadcrumbsProps) {
  if (!items?.length) return null;
  return (
    <Breadcrumb aria-label="Breadcrumb" className="mb-2">
      <BreadcrumbList className="text-sm">
        {items.map((item, index) => {
          const isLast = index === items.length - 1;
          return (
            <Fragment key={`${item.label}-${index}`}>
              {index > 0 ? <BreadcrumbSeparator /> : null}
              <BreadcrumbItem
                className={cn(isLast && "active")}
                aria-current={isLast ? "page" : undefined}
              >
                {!isLast && item.to ? (
                  <BreadcrumbLink render={<Link href={breadcrumbHref(item.to)} />}>
                    {item.label}
                  </BreadcrumbLink>
                ) : isLast ? (
                  <span className="font-normal text-foreground">{item.label}</span>
                ) : (
                  item.label
                )}
              </BreadcrumbItem>
            </Fragment>
          );
        })}
      </BreadcrumbList>
    </Breadcrumb>
  );
}
