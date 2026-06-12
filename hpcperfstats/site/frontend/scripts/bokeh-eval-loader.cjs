/** Webpack loader: indirect eval for Bokeh 3.9 CustomJS / SlickGrid (ported from vite.config.js). */
module.exports = function bokehEvalLoader(source) {
  let next = source;
  const resource = this.resourcePath || "";
  if (resource.includes("customjs.js")) {
    next = next.replace(
      /await eval\(`import\("\$\{url\}"\)`\)/g,
      'await (0, eval)(`import("${url}")`)',
    );
  }
  if (resource.includes("slick.grid.js")) {
    next = next.replace(/return eval\(expr\);/g, "return (0, eval)(expr);");
  }
  return next;
};
