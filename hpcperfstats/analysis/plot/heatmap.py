import os
os.environ['OPENBLAS_NUM_THREADS'] = '4'
from hpcperfstats.analysis.gen import utils
import numpy
from bokeh.plotting import figure
from bokeh.palettes import brewer
from bokeh.models import ColumnDataSource, BasicTicker, ColorBar, LinearColorMapper, PrintfTickFormatter, HoverTool 
from bokeh.transform import transform

class HeatMap():
  
  def plot(self, job):
    u = utils.utils(job)
    schema, _stats = u.get_type("pmc")

    host_cpi = []    
    for hostname, stats in _stats.items():
      cpi = numpy.diff(stats[:, schema["CLOCKS_UNHALTED_CORE"].index])/numpy.diff(stats[:, schema["INSTRUCTIONS_RETIRED"].index])
      host_cpi += [numpy.append(cpi, cpi[-1])]      
    host_cpi = numpy.array(host_cpi).flatten()
    host_cpi = numpy.nan_to_num(host_cpi)    
    times = (job.times - job.times[0]).astype(str)
    data = ColumnDataSource(dict(
      hostnames = [h for host in u.hostnames for h in [host]*len(times)],
      times = list(times)*len(u.hostnames),
      cpi = host_cpi
    ))

    hover = HoverTool(tooltips = [("cpi", "@cpi")])

    mapper = LinearColorMapper(palette = brewer["Spectral"][10][::-1],
                               low = 0.25, high = 2)  
    colors = {"field" : "cpi", "transform" : mapper}
    color_bar = ColorBar(color_mapper = mapper, location = (0,0), 
                         ticker = BasicTicker(desired_num_ticks = 10))

    hm = figure(title = "<Cycles/Instruction> = " + "{0:0.2}".format(host_cpi.mean()), 
                #plot_width = 20*len(times), plot_height = 30*len(u.hostnames),
                x_range = times, logo = None,
                y_range = u.hostnames, tools = [hover]) 
    
    hm.rect("times", "hostnames", source = data, 
            width = 1, height = 1,            
            line_color = None, 
            fill_color = colors)              

    hm.add_layout(color_bar, "right")    
        
    hm.axis.axis_line_color = None
    hm.axis.major_tick_line_color = None
    hm.axis.major_label_text_font_size = "5pt"
    hm.axis.major_label_standoff = 0
    hm.xaxis.major_label_orientation = 1.0
    
    return hm
