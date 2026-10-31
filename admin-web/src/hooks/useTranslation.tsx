import { createContext, useContext, ReactNode, useMemo } from "react";
import en from "../locales/en.json";
import { lookupLocaleKey } from "../utils/i18n";

interface TranslationContextType {
  t: (key: string, params?: Record<string, string | number>) => string;
  locale: string;
}

const TranslationContext = createContext<TranslationContextType | undefined>(undefined);

export function TranslationProvider({ children }: { children: ReactNode }) {
  const locale = "en";

  const contextValue = useMemo(() => ({
    locale,
    t: (key: string, params?: Record<string, string | number>): string => {
      return lookupLocaleKey(en, key, params);
    },
  }), []);

  return (
    <TranslationContext.Provider value={contextValue}>
      {children}
    </TranslationContext.Provider>
  );
}

export function useTranslation() {
  const context = useContext(TranslationContext);
  if (!context) {
    throw new Error("useTranslation must be used within a TranslationProvider");
  }
  return context;
}
