"""Shared Bokeh hover HTML snippets for analysis plots."""


def hover_tooltip_html_host_time_value(value_label, value_field):
  """Build an HTML hover template with spacing between multi-point hits."""
  return f"""
    <div style="padding-bottom:6px; margin-bottom:6px; border-bottom:1px solid #d0d7de;">
      <div><strong>host:</strong> @host</div>
      <div><strong>time:</strong> @_hover_time</div>
      <div><strong>{value_label}:</strong> @{value_field}_plain</div>
    </div>
  """
