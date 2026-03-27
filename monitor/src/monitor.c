#include <dirent.h>
#include <errno.h>
#include <getopt.h>
#include <malloc.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <unistd.h>
#include <sys/fcntl.h>
#include <sys/time.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/syslog.h>
#ifdef __cplusplus
extern "C" {
#endif
#include <ev.h>
#ifdef __cplusplus
}
#endif

#include "daemonize.h"
#include "string1.h"
#include "stats.h"
#include "stats_buffer.h"
#include "trace.h"
#include "pscanf.h"

static char *app_name = NULL;
static char *conf_file_name = NULL;
static FILE *log_stream = NULL;

static char *server = NULL;
static char *queue  = "default";
static char *port   = "5672";

static char *dumpfile_dir = "/tmp/hpcperfstats";
static double freq = 300;
static int max_buffer_size = 300; // 25 hours
static int allow_ring_buffer_overwrite = 0;
static int file_mode_enabled = 0;
static int send_success_count = 0;
static int send_success_count_max = 3;

static ev_timer sample_timer;
static ev_timer rotate_timer;

char jobid[80] = "-";

int nr_cpus;
int n_pmcs;
processor_t processor = 0;

int read_conf_file()
{
  FILE *conf_file_fd = NULL;
  int ret = -1;

  if (conf_file_name == NULL) return 0;

  conf_file_fd = fopen(conf_file_name, "r");

  if (conf_file_fd == NULL) {
    fprintf(log_stream, "Can not open config file: %s, error: %s",
	    conf_file_name, strerror(errno));
    return -1;
  }

  char *line_buf = NULL;
  size_t line_buf_size = 0;
  while(getline(&line_buf, &line_buf_size, conf_file_fd) >= 0) {
    char *line = line_buf;
    char *key = strsep(&line, " :\t=");
    if (key == NULL || line == NULL)
      continue;
    
    while (*line  == ' ') line++;
    if (strcmp(key, "server") == 0) { 
      line[strlen(line) - 1] = '\0';
      server = strdup(line);
      fprintf(log_stream, "%s: Setting server to %s based on file %s\n",
	      app_name, server, conf_file_name);
    }   
    if (strcmp(key, "queue") == 0) { 
      line[strlen(line) - 1] = '\0';
      queue = strdup(line);
      fprintf(log_stream, "%s: Setting queue to %s based on file %s\n",
	      app_name, queue, conf_file_name);
    }
    if (strcmp(key, "port") == 0) {
      line[strlen(line) - 1] = '\0';
      port = strdup(line);
      fprintf(log_stream, "%s: Setting server port to %s based on file %s\n",
	      app_name, port, conf_file_name);
    }
    if (strcmp(key, "buffer") == 0) {
      line[strlen(line) - 1] = '\0';
      max_buffer_size = atoi(line);
      fprintf(log_stream, "%s: Setting buffer size to %d based on file %s\n",
	      app_name, max_buffer_size, conf_file_name);
    }
    if (strcmp(key, "freq") == 0) {  
      if (sscanf(line, "%lf", &freq) == 1)
	fprintf(log_stream, "%s: Setting frequency to %f based on file %s\n",
		app_name, freq, conf_file_name);
    }
  }
  if (line_buf)
    free (line_buf);
  fclose(conf_file_fd);

  return ret;
}

static int send_stats_buffer(struct stats_buffer *sf) {

  size_t i;
  struct stats_type *type;

  /* collect every enabled type */
  i = 0;
  while ((type = stats_type_for_each(&i)) != NULL) {
    if (type->st_enabled) {    
      (*type->st_collect)(type);
    }
  }
  int rc = 0;
  /* Write data to buffer and ship off node */
  if (stats_buffer_write(sf) < 0) 
    rc = -1;

  return rc;
}

static int get_dumpfile_number() {
  DIR *d;
  struct dirent *dir;
  int n_files = 0;
  d = opendir(dumpfile_dir);
  if (d) {
    while ((dir = readdir(d)) != NULL) {
      if (dir->d_type == DT_REG)
          n_files++;
    }
    closedir(d);
  }
  return n_files;
}

/* Get the list of all dumpfiles currently on the node */
static char **get_dumpfile_list() {
  DIR *d;
  struct dirent *dir;
  char **name_list;
  int n_files = get_dumpfile_number();

  d = opendir(dumpfile_dir);
  if (d) {
    name_list = (char**)malloc(sizeof(char*)*n_files);
    int i = 0;
    while ((dir = readdir(d)) != NULL) {
      if (dir->d_type == DT_REG) {
          name_list[i] = (char*)malloc(sizeof(char)*16 + sizeof(dumpfile_dir));
          snprintf(name_list[i], sizeof(char)*16 + sizeof(dumpfile_dir), "%s/%s", dumpfile_dir, dir->d_name);
          i++;
      }
    }
    closedir(d);
  }
  return name_list;
}

/* Get the full path to the current dumpfile */
static char* get_current_dumpfile()
{
  struct timeval tp;
  gettimeofday(&tp, NULL);
  time_t t = tp.tv_sec;
  struct tm * time_info = localtime(&t);
  char *time_str = malloc(sizeof(char) * 16);
  strftime(time_str, 16, "%Y-%m-%d.sf", time_info);

  char *file_str = malloc(sizeof(char) * 64);
  snprintf(file_str, sizeof(char) * 64, "%s/%s", dumpfile_dir, time_str);

  free(time_str);
  return file_str;
}

/* Save a single stats to dumpfile */
static int save_file_stats_buffer(struct stats_buffer *sf) 
{
  int rc = 0;
  char *file_path = get_current_dumpfile();
  rc = stats_buffer_write_file(sf, file_path);
  
  if (rc != 0)
    ERROR("Failed saving stats to dumpfile\n");
  
  free(file_path);
  return rc;
}

/* Save all stats in the ring buffer to dumpfile */
static int save_file_ring_buffer(struct sf_ring_buffer *w)
{
  int rc = 0;
  if (w->q_count == 0) {
    rc = -1;
    goto err;
  }

  char *file_path = get_current_dumpfile();

  struct sf_queue * sf = w->q_first;

  do {
    rc = stats_buffer_write_file(sf->sf, file_path);
    if (rc == -1)
      goto err;
    w->f_count++;
    sf = sf->forward;
  } while (sf != w->q_first);

  err:
    ERROR("Error saving stats to dumpfile %s\n");
    free(file_path);
    return rc;
}

/* Load stats from dumpfiles and resend */
static void send_dumpfile_stats(struct sf_ring_buffer *w)
{
    int rc;
    int n_files = get_dumpfile_number();
    char **file_list = get_dumpfile_list();
    int n_files_deleted = 0;
    for (int i = 0; i < n_files; i++)  {
      FILE *f = fopen(file_list[i], "r");
      rc = ring_buffer_load_file(f, w, server, port, queue, max_buffer_size, allow_ring_buffer_overwrite);
      if (rc == 0) {
        int s = remove(file_list[i]);
        n_files_deleted++;
        fprintf(log_stream, "Resending stats in the ring buffer\n");
        ring_buffer_resend(w);
        if (w->q_count != 0) {
          fprintf(log_stream, "w_q_count = %d\n", w->q_count);
          send_success_count = 0;
          break;
        }
      }
      else {
	fprintf(log_stream, "Error loading stats file %s\n",file_list[i]);
        send_success_count = 0;
        break;
      }
    }
    /* Disable file_mode after the old stats are cleared */
    if (n_files_deleted == n_files)
        file_mode_enabled = 0;

    /* Cleanup */
    for (int i = 0; i < n_files; i++)
      free(file_list[i]);
    free(file_list);
}

static void print_buffer_status(struct sf_ring_buffer *w)
{
  fprintf(log_stream, "status = %d, ", w->status);
  fprintf(log_stream, "allow_overwrite = %d, ", allow_ring_buffer_overwrite);
  fprintf(log_stream, "file_mode = %d, ", file_mode_enabled);
  fprintf(log_stream, "#succ_send = %d/%d\n", send_success_count, send_success_count_max);
  fprintf(log_stream, "#acc_processed = %d, ", w->b_count);
  fprintf(log_stream, "#cur_buffered = %d/%d, ", w->q_count, max_buffer_size);
  fprintf(log_stream, "#acc_succ_sent = %d, ", w->s_count);
  fprintf(log_stream, "#acc_succ_resent = %d\n", w->r_count);
  fprintf(log_stream, "#acc_deleted = %d, ", w->d_count);
  fprintf(log_stream, "#acc_saved = %d, ", w->f_count);
  fprintf(log_stream, "#acc_loaded = %d\n", w->l_count);
}

/* Send header with data based on rotate timer interval */
static void rotate_timer_cb(struct ev_loop *loop, ev_timer *w_, int revents) 
{
  pscanf(JOBID_FILE_PATH, "%79s", jobid);  

  struct sf_ring_buffer *w = (struct sf_ring_buffer *)w_->data;

  struct stats_buffer *sf;
  sf = malloc(sizeof(*sf));
  if (stats_buffer_open(sf, server, port, queue) < 0)
    ERROR("Failed opening data buffer : %m\n");

  size_t i;
  struct stats_type *type;  

  /* Cleanup */
  i = 0;
  while ((type = stats_type_for_each(&i)) != NULL)
    stats_type_destroy(type);    
  
  /* Initialize */
  i = 0;
  while ((type = stats_type_for_each(&i)) != NULL) {
    type->st_enabled = 1;
    if (stats_type_init(type) < 0) {
      type->st_enabled = 0;
      continue;
    }    
    if (type->st_begin != NULL)
      (*type->st_begin)(type);
  }

  stats_wr_hdr(sf);
  w->b_count++;
  w->status = send_stats_buffer(sf);

  if (w->status == 0) {
    w->s_count++;
    stats_buffer_close(sf);
    if (file_mode_enabled == 1)
      send_success_count++;
  }
  else {
    ERROR("Failed sending stats. Adding stats to ring buffer\n");
    send_success_count = 0;
    int rc = ring_buffer_insert(sf, w, max_buffer_size, allow_ring_buffer_overwrite);
    if (rc < 0) {
      ERROR("Failed adding stats to ring buffer. Saving stats to dumpfile\n");
      rc = save_file_stats_buffer(sf);
      stats_buffer_close(sf);
      free(sf);
      if (rc == 0) {
        w->f_count++;
        file_mode_enabled = 1;
      }
    }
  }
  if (w->q_count > 0) {
     fprintf(log_stream, "Resending stats in the ring buffer\n");
     ring_buffer_resend(w);
     if (w->q_count > 0)
      send_success_count = 0;
  }
  /* Print buffer status */
  print_buffer_status(w);
}

/* Send data based on ev timer interval */
static void sample_timer_cb(struct ev_loop *loop, ev_timer *w_, int revents) 
{
  int rc;
  int n_files;
  char **file_list;
  pscanf(JOBID_FILE_PATH, "%79s", jobid);  

  struct sf_ring_buffer *w = (struct sf_ring_buffer *)w_->data;

  struct stats_buffer *sf;
  sf = malloc(sizeof(*sf));
  if (stats_buffer_open(sf, server, port, queue) < 0)
    ERROR("Failed opening data buffer : %m\n");

  w->b_count++;
  w->status = send_stats_buffer(sf);

  if (w->status == 0)  {
    w->s_count++;
    stats_buffer_close(sf);
    free(sf);
    if (file_mode_enabled == 1)
      send_success_count++;
  }
  else {
    ERROR("Failed sending stats. Adding stats to ring buffer\n");
    rc = ring_buffer_insert(sf, w, max_buffer_size, allow_ring_buffer_overwrite);
    send_success_count = 0;
    if (rc < 0) {
      ERROR("Failed adding stats to ring buffer. Saving stats to dumpfile\n");
      rc = save_file_stats_buffer(sf);
      stats_buffer_close(sf);
      free(sf);
      if (rc == 0) {
        w->f_count++;
        file_mode_enabled = 1;
      }
    }
  }

  /* Resend stats in ring buffer */
  if (w->q_count > 0) {
    fprintf(log_stream, "Resending stats in the ring buffer\n");
    ring_buffer_resend(w);
    if (w->q_count != 0)
      send_success_count = 0;
  }
  
  /* Resend stats in dumpfiles */
  if (file_mode_enabled == 1 && w->q_count == 0 && send_success_count >= send_success_count_max) {
    fprintf(log_stream, "Resending stats in the dumpfile\n");
    send_dumpfile_stats(w);
  }

  /* Print buffer status */
  print_buffer_status(w);
}

/* Collect and send data based on IO to JOBID file */
static void fd_cb(EV_P_ ev_io *w_, int revents)
{
  struct sf_ring_buffer *w = (struct sf_ring_buffer *)w_->data;

  struct stats_buffer *sf;
  
  sf = malloc(sizeof(*sf));

  if (stats_buffer_open(sf, server, port, queue) < 0)
    ERROR("Failed opening data buffer : %m\n");

  char new_jobid[80] = "-";
  pscanf(JOBID_FILE_PATH, "%79s", new_jobid);  
  
  if (strcmp(jobid, new_jobid) != 0) { 
    if (strcmp(new_jobid, "-") != 0) {    
      strcpy(jobid, new_jobid);
      fprintf(log_stream, "Loading jobid %s from %s\n", jobid, JOBID_FILE_PATH);	
      stats_buffer_mark(sf, "begin %s", jobid);
      sample_timer.repeat = freq; 
    }
    else {
      fprintf(log_stream, "Unloading jobid %s from %s\n", jobid, JOBID_FILE_PATH);	
      stats_buffer_mark(sf, "end %s", jobid);
      sample_timer.repeat = 3600; 
    }
    ev_timer_again(EV_DEFAULT, &sample_timer);
  }

  size_t i;
  struct stats_type *type;    

  /* Cleanup */
  i = 0;
  while ((type = stats_type_for_each(&i)) != NULL)
    stats_type_destroy(type);    
  
  /* Initialize */
  i = 0;
  while ((type = stats_type_for_each(&i)) != NULL) {
    type->st_enabled = 1;
    if (stats_type_init(type) < 0) {
      type->st_enabled = 0;
      continue;
    }
    if (type->st_begin != NULL)
      (*type->st_begin)(type);
  }

  /* Send stats */
  w->b_count++;
  w->status = send_stats_buffer(sf);

  if (w->status == 0)   {
    w->s_count++;
    stats_buffer_close(sf);
    free(sf);

    if (file_mode_enabled == 1)
      send_success_count++;

    /* Resend stats in ring buffer */
    if (w->q_count > 0) {
      fprintf(log_stream, "Resending stats in the ring buffer\n");
      ring_buffer_resend(w);
      if (w->q_count != 0)
        send_success_count = 0;
    }

    /* Resend stats in dumpfiles */
    if (file_mode_enabled == 1 && w->q_count == 0 && strcmp(new_jobid, "-") == 0 && send_success_count > 0) {
      fprintf(log_stream, "Resending stats in the dumpfile\n");
      send_dumpfile_stats(w);
    }
  }
  else {
    ERROR("Failed sending stats. Adding stats to ring buffer\n");
    int rc = ring_buffer_insert(sf, w, max_buffer_size, allow_ring_buffer_overwrite);
    if (rc < 0) {
      ERROR("Failed adding stats to ring buffer. Saving stats to file\n");
      rc = save_file_stats_buffer(sf);
      stats_buffer_close(sf);
      free(sf);
      if (rc == 0) {
        w->f_count++;
        file_mode_enabled = 1;
        send_success_count = 0;
      }
    }
  }
  strcpy(jobid, new_jobid);

  /* Print buffer status */
  print_buffer_status(w);
}

/* Signal Callbacks for SIGINT (terminate) and SIGHUP (reload conf file) */
static void signal_cb_int(EV_P_ ev_signal *sig, int revents)
{
  size_t i;
  struct stats_type *type;    
  struct sf_ring_buffer *w = (struct sf_ring_buffer *)sig->data;

  /* Dump all buffered stats */
  save_file_ring_buffer(w);

  /* Print buffer status */
  print_buffer_status(w);

  /* Cleanup */
  i = 0;
  while ((type = stats_type_for_each(&i)) != NULL)
    stats_type_destroy(type);    

  fprintf(log_stream, "Stopping hpcperfstatsd\n");
  if (pid_fd != -1) {
    lockf(pid_fd, F_ULOCK, 0);
    close(pid_fd);
  }
  if (pid_file_name != NULL) {
    unlink(pid_file_name);
  }
  ev_break (EV_A_ EVBREAK_ALL);
}

static void signal_cb_hup(EV_P_ ev_signal *sig, int revents) 
{
  struct sf_ring_buffer *w = (struct sf_ring_buffer *)sig->data;
  fprintf(log_stream, "Reloading hpcperfstatsd config file %s\n", conf_file_name);
  read_conf_file();    
  sample_timer.repeat = freq; 
  ev_timer_again(EV_DEFAULT, &sample_timer);
  send_success_count = 0;

  /* Print buffer status */
  print_buffer_status(w);
}

static void usage(void)
{
  fprintf(stderr,
          "Usage: %s [OPTION]... [TYPE]...\n"
          "Collect statistics.\n"
          "\n"
          "Mandatory arguments to long options are mandatory for short options too.\n"
          "  -h, --help         display this help and exit\n"
          "  -c [CONFIGFILE] or --configfile [CONFIGFILE] Configuration file to use.\n"
          "  -s [SERVER]     or --server     [SERVER]     Server to send data.\n"
          "  -q [QUEUE]      or --queue      [QUEUE]      Queue to route data to on RMQ server. \n"
          "  -p [PORT]       or --port       [PORT]       Port to use (5672 is the default).\n"
          "  -t [TMP_DIR]    or --tmp        [TMP_DIR]    Directory for dumpfiles (/tmp/hpcperfstats is the default).\n"
          "  -b [BUFFER]     or --buffer     [BUFFER]     Max size (in # of stats) for temporary in-memory storage (288 is the default).\n"
          "  -f [FREQUENCY]  or --frequency  [FREQUENCY]  Frequency to sample (300 seconds is the default).\n"
          ,
          program_invocation_short_name);
}

int main(int argc, char *argv[])
{
  srand (1);
  int daemonmode = 0;
  char *log_file_name = NULL;

  app_name = argv[0];

  struct option opts[] = {
    { "help",      no_argument, 0, 'h' },
    { "daemon",    no_argument, 0, 'd' },
    { "server",    required_argument, 0, 's' },
    { "queue",     required_argument, 0, 'q' },
    { "port",      required_argument, 0, 'p' },
    { "buffer",    required_argument, 0, 'b' },
    { "conf_file", required_argument, 0, 'c'},
    { "tmp_dir",   required_argument, 0, 't' },
    { "frequency", required_argument, 0, 'f' },
    { NULL, 0, 0, 0 },
  };

  int c;
  while ((c = getopt_long(argc, argv, "hdc:s:q:f:p:b:t:", opts, 0)) != -1) {
    switch (c) {
    case 'd':
      daemonmode = 1;
      break;     
    case 's':
      server = strdup(optarg);
      break;
    case 'f':
      freq = atof(optarg);
      break;
    case 'c':    
      conf_file_name = strdup(optarg);
      break;
    case 'q':
      queue = strdup(optarg);
      break;
    case 'p':
      port = strdup(optarg);
      break;
    case 't':
      dumpfile_dir = strdup(optarg);
      break;
    case 'b':
      max_buffer_size = atoi(optarg);
      break;
    case 'h':
      usage();
      exit(0);
    case '?':
      fprintf(stderr, "Try `%s --help' for more information.\n", program_invocation_short_name);
      exit(1);
    }
  }

  log_stream = stderr;  

  /* Read configuration from config file */
  read_conf_file();

  if (daemonmode) {
    if (pid_file_name == NULL)
      pid_file_name = strdup("/var/run/hpcperfstatsd.pid");
    daemonize();
  }

  fprintf(log_stream, "Started %s\n", app_name);

  /* Create directory for dumpfiles */
  if (mkdir(dumpfile_dir, 0777) < 0) {
    if (errno != EEXIST)
      ERROR("Cannot create directory %s\n", dumpfile_dir);
  }

  if (get_dumpfile_number() > 0) {
    file_mode_enabled = 1;
    send_success_count = 0;
  }

  /* Create and reset ring buffer */
  struct sf_ring_buffer ring_buffer;
  memset(&ring_buffer, 0, sizeof(ring_buffer));

  /* Setup signal callbacks to stop hpcperfstatsd or reload conf file */
  signal(SIGPIPE, SIG_IGN);
  static struct ev_signal sigint;
  sigint.data = (void *)&ring_buffer;
  ev_signal_init(&sigint, signal_cb_int, SIGINT);
  ev_signal_start(EV_DEFAULT, &sigint);

  static struct ev_signal sighup;
  sighup.data = (void *)&ring_buffer;
  ev_signal_init(&sighup, signal_cb_hup, SIGHUP);
  ev_signal_start(EV_DEFAULT, &sighup);
  
  if (server == NULL) {
    fprintf(log_stream, "Must specify a server to send data to with -s [--server] argument or conf file.\n");
    exit(0);
  } else {
    fprintf(log_stream, "hpcperfstatsd data to server %s on port %s.\n", server, port);
  }

  ev_stat fd_watcher;

  /* Initialize timer routine to rotate file */
  rotate_timer.data = (void *)&ring_buffer;
  ev_timer_init(&rotate_timer, rotate_timer_cb, 0.0, 86400);
  ev_timer_start(EV_DEFAULT, &rotate_timer);
  fprintf(log_stream, "Setting hpcperfstatsd rotate log files every %ds\n", 86400);

  /* Initialize callback to respond to writes to job_fd */
  fd_watcher.data = (void *)&ring_buffer;
  ev_stat_init(&fd_watcher, fd_cb, JOBID_FILE_PATH, EV_READ);
  ev_stat_start(EV_DEFAULT, &fd_watcher);
  fprintf(log_stream, "Starting hpcperfstatsd watching fd %s\n", JOBID_FILE_PATH);
  
  /* Initialize timer routine to collect and send data */
  sample_timer.data = (void *)&ring_buffer;
  ev_timer_init(&sample_timer, sample_timer_cb, freq, freq);   
  ev_timer_start(EV_DEFAULT, &sample_timer);
  fprintf(log_stream, "Setting hpcperfstatsd sample frequency to %.1fs\n", freq);

  nr_cpus = sysconf(_SC_NPROCESSORS_ONLN);
  processor = signature(&n_pmcs);

  ev_run(EV_DEFAULT, 0);

  /* Write system log and close it. */
  fprintf(log_stream, "Stopped %s\n", app_name);

  /* Free up names of files */
  if (conf_file_name != NULL) free(conf_file_name);
  if (log_file_name != NULL) free(log_file_name);
  if (pid_file_name != NULL) free(pid_file_name);

  return EXIT_SUCCESS;
}
