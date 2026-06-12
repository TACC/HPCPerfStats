import { createContext, useContext } from "react";

export type SessionData = {
  logged_in?: boolean;
  username?: string;
  is_staff?: boolean;
  is_superuser?: boolean;
  [key: string]: unknown;
};

export const SessionContext = createContext<SessionData | null>(null);

export function useSession(): SessionData | null {
  return useContext(SessionContext);
}
