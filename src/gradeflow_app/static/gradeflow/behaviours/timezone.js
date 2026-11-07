// The server shows times in this browser's time zone, which it reads from a cookie.

export function start() {
  const timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  if (!document.cookie.split("; ").includes(`tz=${timeZone}`)) {
    document.cookie = `tz=${timeZone}; path=/; max-age=31536000; samesite=lax`;
  }
}
