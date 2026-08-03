const REDACTED = "[REDACTED]";

// Mirrors the backend's credential-format checks as browser-side defense in depth.
const SECRET_PATTERNS = [
  /\b(?:xox[baprs]-[a-z0-9-]{8,}|gh[pousr]_[a-z0-9_]{10,}|github_pat_[a-z0-9_]{10,}|lin_api_[a-z0-9_-]{8,}|sk-(?:proj-)?[a-z0-9_-]{12,})\b/gi,
  /\bbearer\s+[a-z0-9._~+/-]{12,}={0,2}\b/gi,
  /\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|secret)\s*[:=]\s*["']?[a-z0-9._~+/-]{12,}={0,2}["']?/gi,
  /\beyJ[a-zA-Z0-9_-]{8,}\.[a-zA-Z0-9_-]{8,}\.[a-zA-Z0-9_-]{8,}\b/g
];

export function redactSecrets(value: string): string {
  return SECRET_PATTERNS.reduce(
    (result, pattern) => result.replace(pattern, REDACTED),
    value
  );
}

export function redactSecretsDeep<T>(value: T): T {
  if (typeof value === "string") return redactSecrets(value) as T;
  if (Array.isArray(value)) return value.map(redactSecretsDeep) as T;
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [key, redactSecretsDeep(item)])
    ) as T;
  }
  return value;
}
