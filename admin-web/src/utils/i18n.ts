/**
 * Pure locale lookup and interpolation helper.
 */
export function lookupLocaleKey(
  dict: Record<string, unknown>,
  key: string,
  params?: Record<string, string | number>
): string {
  if (!key || typeof key !== "string") {
    return "";
  }

  const parts = key.split(".");
  let current: unknown = dict;

  for (const part of parts) {
    if (current && typeof current === "object" && part in current) {
      current = (current as Record<string, unknown>)[part];
    } else {
      current = undefined;
      break;
    }
  }

  if (typeof current !== "string") {
    // Missing key safe fallback: return key itself
    return key;
  }

  if (!params) {
    return current;
  }

  // Token replacement for {param}
  return current.replace(/\{([a-zA-Z0-9_-]+)\}/g, (match, token) => {
    if (token in params) {
      return String(params[token]);
    }
    return match;
  });
}

