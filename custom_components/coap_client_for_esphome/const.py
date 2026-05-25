"""Constants for the CoAP Client integration."""

DOMAIN = "coap_client_for_esphome"
DEFAULT_PORT = 5683
DEFAULT_PING_TIMEOUT_S = 150

CONF_OSCORE = "oscore"
CONF_MASTER_SECRET = "master_secret"
CONF_MASTER_SALT = "master_salt"
CONF_SENDER_ID = "sender_id"
CONF_RECIPIENT_ID = "recipient_id"
CONF_ID_CONTEXT = "id_context"
CONF_OSCORE_SEQ_THRESHOLD = "oscore_seq_threshold"

CONF_SUBSCRIBE_LOGS = "subscribe_logs"

RT_PING = "esphome.ping"
RT_LOG = "esphome.log"
RT_SENSOR = "esphome.sensor"
RT_SWITCH = "esphome.switch"
RT_BINARY_SENSOR = "esphome.binary_sensor"
RT_BUTTON = "esphome.button"
RT_ACTION = "esphome.action"
RT_DEVICE = "esphome.device"
RT_TEXT_SENSOR = "esphome.text_sensor"
RT_NUMBER = "esphome.number"
RT_LOCK = "esphome.lock"
RT_VALVE = "esphome.valve"

# SenML numeric label keys (RFC 8428 Appendix E)
SENML_U = 1  # unit
SENML_V = 2  # numeric value
SENML_VS = 3  # string value
SENML_VB = 4  # boolean value
