import { useMemo } from "react";
import { useParams, usePathname } from "next/navigation";
import {
  parseMachineSlug,
  resolveMachineSlugFromNavigation,
  type MachineFlatRouteParams,
  type MachineRouteView,
} from "@/utils/machine-route-params";

export type MachineRouteParams = {
  slug: string[];
  flatParams: MachineFlatRouteParams;
  view: MachineRouteView;
};

/** Canonical `/machine/**` route segments for static-export client navigation. */
export function useMachineRouteParams(): MachineRouteParams {
  const params = useParams();
  const pathname = usePathname();
  const slug = useMemo(
    () => resolveMachineSlugFromNavigation(params, pathname),
    [params, pathname],
  );
  return useMemo(() => parseMachineSlug(slug), [slug]);
}
