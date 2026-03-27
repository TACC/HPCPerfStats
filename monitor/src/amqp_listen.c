/* Adapted from rabbitmq-c examples/amqp_listen.c */
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <fcntl.h>
#include <getopt.h>
#include <syslog.h>
#include <stdint.h>
#include <unistd.h>
#include <sys/time.h>
#include <sys/types.h>
#include <sys/stat.h>
#include <amqp_tcp_socket.h>
#include <amqp.h>
#include <amqp_framing.h>
#include <assert.h>

#include "string1.h"
#include "trace.h"

static int rpc_ok(amqp_connection_state_t conn, const char *what)
{
  amqp_rpc_reply_t reply = amqp_get_rpc_reply(conn);
  if (reply.reply_type != AMQP_RESPONSE_NORMAL) {
    syslog(LOG_ERR, "%s failed\n", what);
    return 0;
  }
  return 1;
}

int consume(const char *hostname, const char* port, const char* archive_dir)
{

  int rc = 0;
  int status;
  char const *exchange;
  char const *bindingkey;
  amqp_socket_t *socket = NULL;
  amqp_connection_state_t conn;
  amqp_bytes_t queuename = amqp_empty_bytes;
  int channel_opened = 0;
  int logged_in = 0;

  exchange = "amq.direct";
  bindingkey = HOST_NAME_QUEUE;

  conn = amqp_new_connection();
  socket = amqp_tcp_socket_new(conn);
  if (socket == NULL) {
    syslog(LOG_ERR, "Error creating RMQ socket\n");
    rc = 1;
    goto out;
  }

  if (amqp_socket_open(socket, hostname, atoi(port))) {
    syslog(LOG_ERR,"Error opening RMQ server %s on port %s\n",hostname,port);
    rc = 1;
    goto out;
  }
 
  amqp_rpc_reply_t rep = 
    amqp_login(conn, "/", 0, 131072, 0, 
	       AMQP_SASL_METHOD_PLAIN, "guest", "guest");
  if (AMQP_RESPONSE_NORMAL != rep.reply_type) {
    syslog(LOG_ERR,"Error logging into RMQ server %s on port %s\n",
	   hostname,port);
    rc = 1;
    goto out;
  }
  logged_in = 1;
  
  amqp_channel_open(conn, 1);
  if (!rpc_ok(conn, "amqp channel open")) {
    rc = 1;
    goto out;
  }
  channel_opened = 1;
  syslog(LOG_INFO, "Connect to RMQ server on host %s port %s queue %s\n",
	 hostname,port,bindingkey);

  {
    amqp_queue_declare_ok_t *r = 
      amqp_queue_declare(conn, 1, 
			 amqp_cstring_bytes(bindingkey), 
			 0, 1, 0, 0,
			 amqp_empty_table);
    if (!rpc_ok(conn, "queue declare")) {
      rc = 1;
      goto out;
    }
    queuename = amqp_bytes_malloc_dup(r->queue);
    if (queuename.bytes == NULL) {
      syslog(LOG_ERR, "Out of memory while copying queue name\n");
      goto out;
    }
  }

  amqp_queue_bind(conn, 1, queuename, 
		  amqp_cstring_bytes(exchange), 
		  amqp_cstring_bytes(bindingkey),
                  amqp_empty_table);
  if (!rpc_ok(conn, "queue bind")) {
    rc = 1;
    goto out;
  }

  amqp_basic_consume(conn, 1, queuename, 
		     amqp_empty_bytes, 
		     0, 0, 0, amqp_empty_table);
  if (!rpc_ok(conn, "basic consume")) {
    rc = 1;
    goto out;
  }
  setbuf(stdout,NULL);
  // Write data to file in hostname directory
  FILE *fd;
  {
    while (1) {
      amqp_rpc_reply_t res;
      amqp_envelope_t envelope;
      amqp_maybe_release_buffers(conn);

      res = amqp_consume_message(conn, &envelope, NULL, 0);
      if (AMQP_RESPONSE_NORMAL != res.reply_type) {
        break;
      }
      status = amqp_basic_ack(conn, 1, envelope.delivery_tag, 0);
      if (status != AMQP_STATUS_OK) {
        syslog(LOG_ERR, "Error acknowledging RMQ message\n");
        amqp_destroy_envelope(&envelope);
        continue;
      }
      umask(022);

      char *data_buf = NULL;
      size_t body_len = envelope.message.body.len;
      data_buf = (char *) malloc(body_len + 1);
      if (data_buf == NULL) {
        syslog(LOG_ERR, "Out of memory while copying message body\n");
        amqp_destroy_envelope(&envelope);
        continue;
      }
      memcpy(data_buf, envelope.message.body.bytes, body_len);
      data_buf[body_len] = '\0';
      char *tmp_buf = data_buf;
      char *line, *host_token = NULL;
      line = wsep(&data_buf);
      int new_file = 0;
      if (line == NULL) {
        free(tmp_buf);
        amqp_destroy_envelope(&envelope);
        continue;
      }

      if (*(line) == '$') {  // If schema get hostname and start new file
	while(1) {
	  line = wsep(&data_buf);
	  if (line == NULL) {
            break;
          }
	  if (strcmp(line  ,"$hostname") == 0) {
	    host_token = wsep(&data_buf);
	    break;
	  }
	}
	new_file = 1;
      }
      else { // If stats data get hostname
	line = wsep(&data_buf);
	host_token = wsep(&data_buf);
      }	
      if (host_token == NULL || *host_token == '\0') {
        syslog(LOG_ERR, "Cannot extract hostname from RMQ message\n");
        free(tmp_buf);
        amqp_destroy_envelope(&envelope);
        continue;
      }
      //syslog(LOG_INFO, "Consuming stats data from %s\n", hostname);
      // Make directory for host hostname if it doesn't exist
      char *stats_dir_path = strf("%s/%s", archive_dir, host_token);
      free(tmp_buf);
      if (stats_dir_path == NULL) {
        syslog(LOG_ERR, "cannot create stats directory path: %m\n");
        amqp_destroy_envelope(&envelope);
        continue;
      }
      if (mkdir(stats_dir_path, 0777) < 0) {
	if (errno != EEXIST)
	  syslog(LOG_ERR, "cannot create directory `%s': %m\n", stats_dir_path);
      }
      char *current_path = strf("%s/%s",stats_dir_path,"current");
      if (current_path == NULL) {
        syslog(LOG_ERR, "cannot create current path: %m\n");
        free(stats_dir_path);
        amqp_destroy_envelope(&envelope);
        continue;
      }
      // Unlink from old file if starting a new file      
      if (new_file) {
	if (unlink(current_path) < 0 && errno != ENOENT) {
	  syslog(LOG_ERR, "cannot unlink `%s': %m\n", current_path);
	  rc = 1;
          free(stats_dir_path);
          free(current_path);
          amqp_destroy_envelope(&envelope);
	  goto out;
	}
	syslog(LOG_INFO, "Rotating stats file for %s.\n", host_token);

	fd = fopen(current_path, "w");
        if (fd == NULL) {
          syslog(LOG_ERR,"cannot open file: %s\n", current_path);
          free(stats_dir_path);
          free(current_path);
          amqp_destroy_envelope(&envelope);
          continue;
        }
	struct timeval tp;
	double current_time;
	gettimeofday(&tp,NULL);
	current_time = tp.tv_sec+tp.tv_usec/1000000.0;

	// Link to new file which will be left behind after next rotation
	char *link_path = strf("%s/%ld", stats_dir_path, (long)current_time);
	if (link_path == NULL)
	  syslog(LOG_ERR, "cannot create path: %m\n");
	else if (link(current_path, link_path) < 0)
	  syslog(LOG_ERR, "cannot link `%s' to `%s': %m\n", current_path, link_path);
	free(link_path);	  	 
      }
      else {
	fd = fopen(current_path, "a");
	if (fd == NULL) {
	  syslog(LOG_ERR,"cannot open file: %s\n", current_path);
	  free(stats_dir_path);
	  free(current_path);
          amqp_destroy_envelope(&envelope);
	  continue;
	}
      }
      fprintf(fd,"%.*s",
	      (int) envelope.message.body.len, 
	      (char *) envelope.message.body.bytes);
      fflush(fd);
      fclose(fd);
      free(stats_dir_path);
      free(current_path);
      amqp_destroy_envelope(&envelope);
    }
  }
  
  rc = 0;
 out:
  if (queuename.bytes != NULL)
    amqp_bytes_free(queuename);
  if (channel_opened)
    amqp_channel_close(conn, 1, AMQP_REPLY_SUCCESS);
  if (logged_in)
    amqp_connection_close(conn, AMQP_REPLY_SUCCESS);
  amqp_destroy_connection(conn);
  return rc;
}

int main(int argc, char *argv[])
{

  /* Our process ID and Session ID */
  pid_t pid, sid;
  
  /* Fork off the parent process */
  pid = fork();
  if (pid < 0) {
    exit(EXIT_FAILURE);
  }
  /* If we got a good PID, then
     we can exit the parent process. */
  if (pid > 0) {
    exit(EXIT_SUCCESS);
  }
  /* Create a new SID for the child process */
  sid = setsid();
  if (sid < 0) {
    /* Log the failure */
    exit(EXIT_FAILURE);
  }
  /* Change the current working directory */
  if ((chdir("/")) < 0) {
    /* Log the failure */
    exit(EXIT_FAILURE);
  }

  /* Daemon Specific initialization */
  char *hostname = NULL;
  char *archive_dir = NULL;
  char *port = NULL;

  if (argc < 3) {
    syslog(LOG_ERR,"Usage: command amqp_listend -s SERVER -a ARCHIVE_DIR -p PORT (5672 is default)\n");
    exit(1);
  }

  struct option opts[] = {
    { "server", required_argument, 0, 's' },
    { "port", required_argument, 0, 'p' },
    { "archive_dir", required_argument, 0, 'a' },
    { NULL, 0, 0, 0 },
  };

  int c;
  while ((c = getopt_long(argc, argv, "s:p:a:", opts, 0)) != -1) {
    switch (c) {
    case 's':
      hostname = optarg;
      continue;
    case 'p':
      port = optarg;
      continue;
    case 'a':
      archive_dir = optarg;
      continue;
    }
  }

  if (hostname == NULL) {
    syslog(LOG_ERR, "Must specify a RMQ server with -s [--server] argument.\n");
    exit(1);
  }
  if (archive_dir == NULL) {
    syslog(LOG_ERR, "Must specify an archive dir -a [--archive_dir] argument.\n");
    exit(1);
  }
  if (port == NULL) {
    port = "5672";
  }
        
  /* Close out the standard file descriptors */
  close(STDIN_FILENO);
  close(STDOUT_FILENO);
  close(STDERR_FILENO);
  syslog(LOG_INFO, "Starting hpcperfstats consuming daemon.\n");
  consume(hostname, port, archive_dir);

  exit(EXIT_SUCCESS);
}
