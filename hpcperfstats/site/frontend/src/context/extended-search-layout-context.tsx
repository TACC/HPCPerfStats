import { createContext, useContext } from "react";

export const ExtendedSearchLayoutContext = createContext({
  openExtendedSearch: () => {},
});

export function useExtendedSearchLayout() {
  return useContext(ExtendedSearchLayoutContext);
}
