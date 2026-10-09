export function getDetailPath(pathname: string) {
  const path = pathname.replace(/\/+$/, "");
  return /^\/(pulls|runs)\/[^/]+$/.test(path) ? path : null;
}
