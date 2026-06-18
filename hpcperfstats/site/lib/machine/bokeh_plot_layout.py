"""Bokeh layout tweaks for zoom / overlay-sized job plots (shared by API and artifact persist)."""
import copy


def _apply_zoom_layout_to_bokeh_model(root_model):
  """Best-effort layout so zoom plots stretch to overlay width with intrinsic height (scroll)."""
  try:
    candidates = [root_model]
    if hasattr(root_model, "select"):
      try:
        candidates.extend(list(root_model.select({})))
      except Exception:
        pass
    for model in candidates:
      if hasattr(model, "sizing_mode"):
        model.sizing_mode = "stretch_width"
      if hasattr(model, "width_policy"):
        model.width_policy = "max"
      if hasattr(model, "height_policy"):
        model.height_policy = "auto"
      if hasattr(model, "width"):
        model.width = 1600
      if hasattr(model, "height"):
        model.height = 900
  except Exception:
    pass


def _apply_zoom_layout_to_json_item(plot_item):
  """Return a zoom-sized json_item clone from cached plot data."""
  if not isinstance(plot_item, dict):
    return plot_item
  try:
    cloned = copy.deepcopy(plot_item)
  except Exception:
    return plot_item

  layout_model_names = {
      "Figure",
      "Plot",
      "GridPlot",
      "Row",
      "Column",
      "ToolbarBox",
      "Tabs",
      "TabPanel",
  }

  def _apply_attrs(attrs, apply_layout_sizing=False, allow_dimension_reset=False):
    if not isinstance(attrs, dict):
      return
    if not apply_layout_sizing:
      return
    attrs["sizing_mode"] = "stretch_width"
    attrs["width_policy"] = "max"
    attrs["height_policy"] = "auto"
    if allow_dimension_reset:
      if "width" in attrs:
        attrs["width"] = None
      if "height" in attrs:
        attrs["height"] = None
    if "min_width" in attrs and attrs["min_width"] is None:
      attrs["min_width"] = 600
    if "min_height" in attrs and attrs["min_height"] is None:
      attrs["min_height"] = 320

  def _walk(node):
    if isinstance(node, dict):
      attrs = node.get("attributes")
      if isinstance(attrs, dict):
        model_name = node.get("name")
        is_layout_model = model_name in layout_model_names
        _apply_attrs(
            attrs,
            apply_layout_sizing=is_layout_model,
            allow_dimension_reset=is_layout_model,
        )
      for value in node.values():
        _walk(value)
    elif isinstance(node, list):
      for value in node:
        _walk(value)

  try:
    _walk(cloned)
  except Exception:
    return plot_item
  return cloned
