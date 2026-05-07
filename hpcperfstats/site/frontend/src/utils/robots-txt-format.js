/** @param {readonly string[]} allowPrefixes */
export function formatRobotsTxtBody(allowPrefixes) {
  const lines = ["User-agent: *"];
  for (const prefix of allowPrefixes) {
    lines.push(`Allow: ${prefix}`);
  }
  lines.push("Disallow: /");
  return lines.join("\n");
}
