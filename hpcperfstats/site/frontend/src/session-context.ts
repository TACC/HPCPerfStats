import { createContext, useContext } from "react";
import type { SessionInfo } from "@/api/generated/models/sessionInfo";

export type SessionData = SessionInfo;

export const SessionContext = createContext<SessionData | null>(null);

export function useSession(): SessionData | null {
  return useContext(SessionContext);
}
