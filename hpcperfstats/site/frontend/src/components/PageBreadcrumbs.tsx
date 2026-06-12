import Link from "next/link";

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
    <nav className="mb-2" aria-label="Breadcrumb">
      <ol className="breadcrumb small mb-0">
        {items.map((item, index) => {
          const isLast = index === items.length - 1;
          return (
            <li
              key={`${item.label}-${index}`}
              className={`breadcrumb-item${isLast ? " active" : ""}`}
              aria-current={isLast ? "page" : undefined}
            >
              {!isLast && item.to ? (
                <Link href={item.to}>{item.label}</Link>
              ) : (
                item.label
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
