import errno
import random
import socket
import time
import traceback
import re
import urllib.parse

from lib import bunq_api
from lib import network
from lib import sync
from lib import helpers
from lib.config import config
from lib.log import log


# ----- Parse command line arguments

config.parser.add_argument("--port", type=int,
    help="TCP port number for the server to listen on. For Railway, set this to $PORT (usually 8080)")
config.parser.add_argument("--external-port", type=int,
    help="TCP port number to register for callback. Not needed for Railway deployments")
# Don't set defaults here.  A default looks like a command line parameter,
# so lib.config would ignore an entry in config.json
config.parser.add_argument("--wait", type=int,
    help="Synch time when there is no callback.  Default 60 minutes (1 hour)")
config.parser.add_argument("--interval", type=int,
    help="Synch time with callback.  Defaults 240 minutes (4 hours)")
config.parser.add_argument("--refresh", type=int,
    help="Time to refresh callback setup.  Defaults 480 minutes (8 hours)")
config.parser.add_argument("--callback-host",
    help="Hostname to use in callback (e.g., example.up.railway.app for Railway). "
         "When specified, uses port 443 in callback URL without port number. Defaults to host public IP")
config.parser.add_argument("--callback-marker",
    help="Unique marker for callbacks.  Defaults bunq2ynab-autosync")
config.parser.add_argument("--skip-ip-validation", action='store_true',
    help="Skip validating callback source IP against BUNQ server ranges. Use when behind proxies like Railway")
config.load()


serversocket = None
callback_host = None
callback_port = None
local_port = None
portmap_port = None
sync_obj = None


# ----- Synchronize with YNAB

def synchronize():
    try:
        log.info("Starting sync at " + time.strftime('%Y-%m-%d %H:%M:%S'))
        sync_obj.synchronize()
        log.info("Finished sync at " + time.strftime('%Y-%m-%d %H:%M:%S'))
    except Exception as e:
        log.error("Error during synching: {}".format(e))
        log.error(traceback.format_exc())


# ----- Listen for bunq calls and run scheduled jobs

def bind_port():
    serversocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    port = config.get("port")
    if port:
        serversocket.bind(('0.0.0.0', int(port)))
        return serversocket, int(port)
    port = None
    for i in range(0, 128):
        port = random.randint(1025, 65535)
        try:
            serversocket.bind(('0.0.0.0', port))
            return serversocket, port
        except OSError as e:
            if e.errno == errno.EADDRINUSE:
                log.warning("Port {0} is in use, trying next...".format(port))
                continue
            raise
    raise Exception("No free port found")


# ----- Setup callback, wait for callback, teardown

def setup_callback():
    global serversocket, callback_host, callback_port, local_port, portmap_port

    # Don't try to map ports if we have a public IP
    callback_host = callback_port = None
    using_portmap = False
    callback_host = config.get("callback_host")
    if not callback_host:
        local_ip = network.get_local_ip()
        if not network.is_private_ip(local_ip):
            log.info("Host has a public IP...")
            callback_host = local_ip
        elif config.get("port"):
            log.info("Host has a private IP.  A port is specified so we will not "
                     "attempt to map a port.  Remember to configure forward "
                     "manually.")
            callback_host = network.get_public_ip()
        else:
            log.info("Host has a private IP, trying upnp port mapping...")
            network.portmap_setup()
            network.portmap_search()
            callback_host = network.get_public_ip()
            using_portmap = True

    if not callback_host:
        log.error("No public IP found, not registering callback.")
        return

    # Log if we're skipping IP validation
    if config.get("skip_ip_validation", False):
        log.info("IP validation is disabled. This should only be used when behind proxies like Railway")

    if not serversocket:
        serversocket, local_port = bind_port()
        log.info("Listening on port {0}...".format(local_port))
        serversocket.listen(5)  # max incoming calls queued

    marker = config.get("callback_marker") or "bunq2ynab-autosync"
    external_port = config.get("external_port")

    # Check if callback host was explicitly provided by user
    explicit_callback_host = config.get("callback_host") is not None

    if not using_portmap:
        callback_port = external_port or local_port
    elif external_port:
        log.info("Forwarding specified port {}...".format(external_port))
        network.portmap_add(external_port, local_port, marker)
        callback_port = external_port  # Regardless of success
    else:
        log.info("Looking for port to forward...")
        portmap_port = network.portmap_seek(local_port, marker)
        if not portmap_port:
            log.error("Failed to map port, not registering callback.")
            return
        log.info("Succesfully forwarded port {}".format(portmap_port))
        callback_port = portmap_port

    # For explicit callback hosts (like Railway), use port 443 for the callback URL
    # since they handle HTTPS termination and routing.
    #
    # Railway deployment guide:
    # 1. Set --port to $PORT env var (usually 8080) for the internal web server
    # 2. Set --callback-host to your Railway app URL (e.g. myapp.up.railway.app)
    # 3. Do NOT set --external-port as it's not needed with Railway's proxy
    callback_url_port = callback_port
    if explicit_callback_host:
        log.info("Using explicit callback host {}, setting callback URL port to 443".format(callback_host))
        callback_url_port = 443

    if callback_url_port != 443:
        log.warning("Callbacks port is {}.  Callbacks are "
                    "broken for ports other than 443".format(callback_url_port))

    for uid in sync_obj.get_bunq_user_ids():
        # For port 443, omit the port from the URL (standard HTTPS)
        # Railway and similar platforms expect a clean URL without port numbers
        if callback_url_port == 443:
            url = "https://{}/{}".format(callback_host, marker)
        else:
            url = "https://{}:{}/{}".format(callback_host, callback_url_port, marker)
        log.info("Registering callback URL: {}".format(url))
        bunq_api.add_callback(uid, marker, url)


def wait_for_callback():
    refresh = (config.get("refresh") or 8*60)*60
    interval = (config.get("interval") or 4*60)*60
    last_sync = time.time()
    next_refresh = time.time() + refresh
    next_sync = time.time() + interval
    while True:
        time_left = max(min(next_sync, next_refresh) - time.time(), 0)
        log.info("Waiting for callback for {}...".format(
              helpers.format_seconds(time_left)))
        serversocket.settimeout(time_left)
        try:
            (clientsocket, address) = serversocket.accept()
            source_ip = address[0]

            # Process the HTTP request
            try:
                request_data = clientsocket.recv(4096).decode('utf-8', errors='ignore')

                # Verify the request is for our callback endpoint
                marker = config.get("callback_marker") or "bunq2ynab-autosync"
                request_line_match = re.search(r'^(GET|POST)\s+(/[^\s]*)', request_data, re.MULTILINE)

                is_valid_callback = False
                if request_line_match:
                    path = request_line_match.group(2)
                    decoded_path = urllib.parse.unquote(path)
                    log.info("Received request for path: {}".format(decoded_path))

                    # Check if the path matches our callback marker
                    if decoded_path == '/' + marker or decoded_path.startswith('/' + marker + '/'):
                        is_valid_callback = True
                    else:
                        log.warning("Request path does not match callback marker")

                # Only process valid callback requests
                if is_valid_callback:
                    # Look for X-Real-IP header
                    real_ip_match = re.search(r'X-Real-IP:\s*([^\s\r\n]+)', request_data, re.IGNORECASE)
                    if real_ip_match:
                        real_ip = real_ip_match.group(1).strip()
                        log.info("X-Real-IP header found: {}".format(real_ip))
                        source_ip = real_ip

                    # If X-Real-IP not found, try X-Forwarded-For
                    elif not real_ip_match:
                        forwarded_for_match = re.search(r'X-Forwarded-For:\s*([^\s\r\n,]+)', request_data, re.IGNORECASE)
                        if forwarded_for_match:
                            forwarded_ip = forwarded_for_match.group(1).strip()
                            log.info("X-Forwarded-For header found: {}".format(forwarded_ip))
                            source_ip = forwarded_ip
                else:
                    log.info("Ignoring request that doesn't match callback path")
                    source_ip = None  # Skip the validation check below

                # Send HTTP 200 OK response
                response = "HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK"
                clientsocket.sendall(response.encode('utf-8'))
            except Exception as e:
                log.warning("Error processing request: {}".format(e))
            finally:
                clientsocket.close()

            # Only validate and process if this is a request we care about
            if source_ip is not None:
                log.info("Incoming call from {}...".format(source_ip))
                skip_ip_validation = config.get("skip_ip_validation", False)
                if not skip_ip_validation and not network.is_bunq_server(source_ip):
                    log.warning("Source {} not in BUNQ range. Use --skip-ip-validation if behind a proxy".format(source_ip))
                    continue
                elif skip_ip_validation:
                    log.info("Skipping IP validation as configured")
        except socket.timeout as e:
            pass

        if next_refresh <= time.time():
            return
        if time.time() < last_sync + 30:
            next_sync = last_sync + 30
        else:
            log.info("Synchronizing...")
            synchronize()
            last_sync = time.time()
            next_sync = last_sync + interval


def teardown_callback():
    log.info("Cleaning up...")
    callback_marker = config.get("callback_marker") or "bunq2ynab-autosync"
    for uid in sync_obj.get_bunq_user_ids():
        try:
            bunq_api.remove_callback(uid, callback_marker)
        except Exception as e:
            log.info("Error removing callback: {}".format(e))
    try:
        network.portmap_remove(portmap_port)
    except Exception as e:
        log.error("Error removing upnp port mapping: {}".format(e))


def on_error_wait_secs(consecutive_errors):
    if consecutive_errors < 3:
        return 60
    if consecutive_errors < 6:
        return 5*60
    return 60*60


# ----- Main loop
try:
    consecutive_errors = 0
    wait = (config.get("wait") or 1) * 60
    next_sync = 0
    while True:
        try:
            sync_obj = sync.Sync()
            sync_obj.populate()

            if next_sync < time.time():
                log.info("Synchronizing at start or before refresh...")
                synchronize()
                next_sync = time.time() + wait

            setup_callback()
            if callback_host and callback_port:
                wait_for_callback()
            else:
                time_left = max(next_sync - time.time(), 0)
                log.warning("No callback, waiting for {} minutes...".format(
                    helpers.format_seconds(int(time_left/60))))
                time.sleep(time_left)

            consecutive_errors = 0
        except Exception as e:
            short = "Bunq2ynab autosync error: {}".format(e)
            descr = traceback.format_exc()
            log.error(short)
            log.error(descr)
            consecutive_errors += 1
            mail_after_errors = int(config.get("mail_after_errors", 5))
            if mail_after_errors <= consecutive_errors:
                network.send_mail(short, descr)
            else:
                log.info("No mail until {} errors".format(mail_after_errors))
            wait_secs = on_error_wait_secs(consecutive_errors)
            log.error("Failed {} times, waiting {} seconds for retry.".format(
                consecutive_errors, wait_secs))
            time.sleep(wait_secs)
finally:
    teardown_callback()
