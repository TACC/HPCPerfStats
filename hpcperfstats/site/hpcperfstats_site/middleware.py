"""ProfileMiddleware: add ?prof to a URL to profile the view (DEBUG only). Optional ?sort and ?count.

AI generated.
"""
try:
  import cProfile as profile
except ImportError:
  import profile
import pstats

from cStringIO import StringIO
from django.conf import settings


class ProfileMiddleware(object):
  """Simple profile middleware to profile django views. Add ?prof to URL; optional ?sort and ?count.

    AI generated.
    """

  def can(self, request):
    """Return True if DEBUG and request has ?prof.

        AI generated.
        """
    return settings.DEBUG and 'prof' in request.GET

  #and request.user is not None and request.user.is_staff

  def process_view(self, request, callback, callback_args, callback_kwargs):
    """Run callback under profiler when can(request); store profiler for process_response.

        AI generated.
        """
    if self.can(request):
      self.profiler = profile.Profile()
      args = (request,) + callback_args
      try:
        return self.profiler.runcall(callback, *args, **callback_kwargs)
      except:
        # we want the process_exception middleware to fire
        # https://code.djangoproject.com/ticket/12250
        return

  def process_response(self, request, response):
    """If prof was active, replace response content with profiler stats (pre).

        AI generated.
        """
    if self.can(request):
      self.profiler.create_stats()
      io = StringIO()
      stats = pstats.Stats(self.profiler, stream=io)
      stats.strip_dirs().sort_stats(request.GET.get('sort', 'time'))
      stats.print_stats(int(request.GET.get('count', 100)))
      response.content = '<pre>%s</pre>' % io.getvalue()
    return response
