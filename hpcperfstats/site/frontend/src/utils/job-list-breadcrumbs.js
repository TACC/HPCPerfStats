/**
 * @param {Record<string, string | undefined>} routeParams
 * @param {string} [terminalLabel]
 * @returns {Array<{ label: string, to?: string }>}
 */
export function buildJobListBreadcrumbs(routeParams, terminalLabel = "Jobs") {
  const items = [{ label: "Browse", to: "/" }];
  if (routeParams.year) {
    items.push({ label: `Year ${routeParams.year}`, to: `/year/${routeParams.year}` });
  } else if (routeParams.date) {
    items.push({ label: `Date ${routeParams.date}`, to: `/date/${routeParams.date}` });
  } else if (routeParams.username) {
    items.push({
      label: `User ${routeParams.username}`,
      to: `/username/${encodeURIComponent(routeParams.username)}`,
    });
  } else if (routeParams.account) {
    items.push({
      label: `Project ${routeParams.account}`,
      to: `/account/${encodeURIComponent(routeParams.account)}`,
    });
  } else if (routeParams.queue) {
    items.push({
      label: `Queue ${routeParams.queue}`,
      to: `/queue/${encodeURIComponent(routeParams.queue)}`,
    });
  } else if (routeParams.host) {
    items.push({
      label: `Host ${routeParams.host}`,
      to: `/host/${encodeURIComponent(routeParams.host)}`,
    });
  }
  items.push({ label: terminalLabel });
  return items;
}
