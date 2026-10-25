export async function getJSON(path, params = {}) {
  const qs = new URLSearchParams(params).toString();
  const res = await fetch(`/api${path}${qs ? `?${qs}` : ""}`);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail || detail;
    } catch {
      /* body was not JSON */
    }
    throw new Error(detail);
  }
  return res.json();
}
