"""paho-mqtt connection to HSL HFP, with the sandbox workaround for R7."""

import socket
import ssl
import threading
import uuid

from .core import HSL_HOST, TRANSPORTS


def _unix_socketpair():
    a, b = socket.socketpair()
    a.setblocking(False)
    b.setblocking(False)
    return a, b


def connect(transport, topics, on_message, timeout_s=15):
    """Connect to HSL, subscribe, and start paho's network loop in a background thread.

    Returns the client. Raises ConnectionError if there is no CONNACK within timeout_s.
    """
    import paho.mqtt.client as mqtt

    # R7: paho's wake-up pipe is a loopback TCP listen/accept on 127.0.0.1. The sandbox on
    # Standard-access clusters and serverless blocks it, so loop_start() hangs in accept().
    # An AF_UNIX socketpair gives paho the same pipe. Patched here, not at import, so it also
    # applies if this module is ever shipped by value (R8): module-level code doesn't run there.
    mqtt._socketpair_compat = _unix_socketpair

    port, paho_transport = TRANSPORTS[transport]
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"dbx-hsl-{uuid.uuid4().hex[:8]}",
        transport=paho_transport,
    )
    client.tls_set(cert_reqs=ssl.CERT_REQUIRED)
    if paho_transport == "websockets":
        client.ws_set_options(path="/")

    connected = threading.Event()

    def on_connect(c, userdata, flags, reason_code, properties):
        if reason_code == 0:
            # Also runs on automatic reconnects, which re-subscribes.
            c.subscribe([(t, 0) for t in topics])
            connected.set()

    client.on_connect = on_connect
    client.on_message = on_message
    # connect_async: the TCP/TLS handshake runs on paho's daemon thread, so a stalled
    # handshake can't hang the Spark driver.
    client.connect_timeout = timeout_s
    client.connect_async(HSL_HOST, port, keepalive=60)
    client.loop_start()
    if not connected.wait(timeout_s):
        # Don't loop_stop(): it joins the network thread, which may be stuck in the handshake.
        client._thread_terminate = True
        raise ConnectionError(f"No CONNACK from {HSL_HOST}:{port} ({transport}) in {timeout_s}s")
    return client
