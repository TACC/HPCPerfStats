import { Link } from "react-router-dom";

/**
 * @param {{ items: Array<{ label: string, to?: string }> }} props
 */
export default function PageBreadcrumbs({ items }) {
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
                <Link to={item.to}>{item.label}</Link>
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
