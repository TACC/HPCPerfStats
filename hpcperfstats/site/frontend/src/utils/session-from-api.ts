import type { SessionInfo } from "@/api/generated/models/sessionInfo";
import { SITE_MACHINE_NAME } from "@/config/site-identity";
import type { SessionData } from "@/session-context";

/**
 * Map `/api/session/` JSON onto the SPA session object.
 *
 * Must copy every staff-visible flag the layout reads. Dropping
 * `separate_test_login` hides Create test user even when the INI is on.
 */
export function sessionFromApi(data: SessionInfo): SessionData {
  return {
    logged_in: data.logged_in,
    username: data.username,
    is_staff: data.is_staff,
    machine_name: data.machine_name ?? SITE_MACHINE_NAME,
    separate_test_login: Boolean(data.separate_test_login),
  };
}
