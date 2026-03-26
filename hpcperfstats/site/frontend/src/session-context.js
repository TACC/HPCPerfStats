import { createContext, useContext } from "react";

export const SessionContext = createContext(null);

export function useSession() {
  return useContext(SessionContext);
}
