import en from "../locales/en.json";

export function t(key: string): string {
  const parts = key.split(".");
  let cur: unknown = en;
  for (const part of parts) {
    if (cur && typeof cur === "object" && part in cur) {
      cur = (cur as Record<string, unknown>)[part];
    } else {
      return key;
    }
  }
  return typeof cur === "string" ? cur : key;
}

