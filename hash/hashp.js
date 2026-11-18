async function hashStrong(text) {
  const data = new TextEncoder().encode(text);
  const hashBuffer = await crypto.subtle.digest("SHA-256", data);
  const hashArray = Array.from(new Uint8Array(hashBuffer));
  return hashArray.map(function (b) { return b.toString(16).padStart(2, "0"); }).join("");
}
