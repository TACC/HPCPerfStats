import { usePathname } from "next/navigation";
import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";
import { Menu } from "lucide-react";
import { useRouteFocusMain } from "../utils/useRouteFocusMain";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const PUB_LOGIN_PROMPT_HREF = `/login_prompt?next=${encodeURIComponent("/machine/")}`;

type LayoutPubProps = {
  machineName?: string | null;
  children: ReactNode;
};

export default function LayoutPub({ machineName, children }: LayoutPubProps) {
  const pathname = usePathname();
  const [navOpen, setNavOpen] = useState(false);
  useRouteFocusMain(pathname);
  useEffect(() => {
    setNavOpen(false);
  }, [pathname]);

  return (
    <div className="w-full px-4 lg:px-6">
      <header className="relative border-b bg-muted/40" role="navigation" aria-label="Primary">
        <div className="relative flex flex-wrap items-start gap-3 py-3 lg:pb-4">
          <Link
            href="/pub/cluster-dashboard"
            className="site-header-logo flex shrink-0 items-center py-1 lg:min-h-[4.75rem]"
          >
            <img
              src="/media/logo.png"
              alt="TACC — HPCPerfStats home"
              className="h-12 w-auto max-h-[3.25rem] object-contain lg:h-full lg:max-h-[3.25rem]"
            />
          </Link>
          <Button
            type="button"
            variant="outline"
            size="icon-sm"
            className="ml-auto lg:hidden"
            onClick={() => setNavOpen((o) => !o)}
            aria-expanded={navOpen}
            aria-controls="navbar-main-pub"
            aria-label="Toggle navigation"
          >
            <Menu className="size-4" />
          </Button>
          <div
            id="navbar-main-pub"
            className={cn(
              "w-full basis-full lg:static lg:flex lg:flex-1 lg:items-start lg:justify-end",
              navOpen ? "flex flex-col gap-3 border-t pt-3" : "hidden lg:flex",
            )}
          >
            <div className="mx-auto flex min-h-20 max-w-[min(48vw,680px)] flex-col items-center justify-center gap-0.5 break-words text-center lg:absolute lg:left-1/2 lg:-translate-x-1/2">
              <div className="text-xl font-semibold text-foreground">HPCPerfStats</div>
              <div className="text-sm text-muted-foreground">
                a job-level resource usage monitoring tool
              </div>
              {machineName ? (
                <div className="site-header-cluster text-[1.05em] text-muted-foreground">
                  {machineName}
                </div>
              ) : null}
            </div>
            <div className="flex w-full flex-col items-stretch gap-2 max-lg:w-full lg:max-w-[min(42vw,520px)] lg:items-end">
              <div className="flex flex-wrap items-center justify-start gap-2 lg:justify-end">
                <Button variant="outline" size="sm" render={<a href={PUB_LOGIN_PROMPT_HREF} />}>
                  Login to see individual job data
                </Button>
              </div>
            </div>
          </div>
        </div>
      </header>
      <main id="main-content" className="mt-4 outline-none" tabIndex={-1}>
        {children}
      </main>
    </div>
  );
}
