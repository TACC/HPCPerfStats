import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { useDocumentTitle } from "../utils/useDocumentTitle";

export default function PageNotFound() {
  useDocumentTitle("Page not found");
  return (
    <Alert>
      <h1 className="text-2xl font-semibold tracking-tight">Page not found</h1>
      <AlertDescription className="text-muted-foreground">
        That address is not part of HPCPerfStats. Check the URL or return to browse jobs.
      </AlertDescription>
      <Button size="sm" className="mt-3" render={<Link href="/" />}>
        Browse jobs
      </Button>
    </Alert>
  );
}
