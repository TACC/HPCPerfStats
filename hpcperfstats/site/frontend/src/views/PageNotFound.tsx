import Link from "next/link";
import { useDocumentTitle } from "../utils/useDocumentTitle";

export default function PageNotFound() {
  useDocumentTitle("Page not found");
  return (
    <div role="alert">
      <h1 className="h2 mb-3">Page not found</h1>
      <p className="text-muted">
        That address is not part of HPCPerfStats. Check the URL or return to browse jobs.
      </p>
      <Link href="/" className="btn btn-primary btn-sm">
        Browse jobs
      </Link>
    </div>
  );
}
