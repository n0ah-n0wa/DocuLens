import { appConfig, MEBIBYTE } from "@/lib/config";

export function formatBytes(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  if (bytes < MEBIBYTE) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }
  return `${(bytes / MEBIBYTE).toFixed(1)} MB`;
}

export function formatTimestamp(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return iso;
  }
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

export function maxUploadBytes(): number {
  return appConfig.maxFileSizeMb * MEBIBYTE;
}
