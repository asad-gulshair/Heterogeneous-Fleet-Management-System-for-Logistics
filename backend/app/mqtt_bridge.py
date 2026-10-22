"""Optional bridge to a real MQTT broker (Mosquitto).

If the environment variable MQTT_HOST is set (docker-compose does this), every VDA 5050 message
that TusCore exchanges with the tote AMRs is also published on the broker under its real topic
(uagv/v2/<manufacturer>/<serial>/order|state). Watch it with:

    mosquitto_sub -h localhost -t "uagv/#" -v

Without MQTT_HOST everything still works with the in-process message bus.
"""
from __future__ import annotations
import os


class MqttBridge:
    def __init__(self):
        self.host = os.environ.get("MQTT_HOST")
        self.port = int(os.environ.get("MQTT_PORT", "1883"))
        self.connected = False
        self.published = 0
        self.error = None
        self.client = None
        if not self.host:
            return
        try:
            import paho.mqtt.client as mqtt
            self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="tuscore-fleet-manager")
            self.client.on_connect = lambda c, u, f, rc, p=None: setattr(self, "connected", not rc.is_failure)
            self.client.on_disconnect = lambda *a, **k: setattr(self, "connected", False)
            self.client.connect_async(self.host, self.port, keepalive=30)
            self.client.loop_start()                      # network loop in a background thread
        except Exception as e:                            # broker missing / library missing: keep running
            self.error = f"{type(e).__name__}: {e}"
            self.client = None

    def publish(self, topic: str, payload: str):
        if self.client is not None and self.connected:
            self.client.publish(topic, payload, qos=0)
            self.published += 1

    def status(self) -> dict:
        if not self.host:
            return {"enabled": False, "label": "In-process bus (set MQTT_HOST to use Mosquitto)"}
        return {"enabled": True, "host": self.host, "port": self.port, "connected": self.connected,
                "published": self.published, "error": self.error,
                "label": f"Mosquitto {self.host}:{self.port} - " + ("connected" if self.connected else "connecting...")}
