"""Constants for the CoAP Client integration."""

DOMAIN = "esphome_coap_client"
DEFAULT_PORT = 5683

CONF_OSCORE = "oscore"
CONF_MASTER_SECRET = "master_secret"
CONF_MASTER_SALT = "master_salt"
CONF_SENDER_ID = "sender_id"
CONF_RECIPIENT_ID = "recipient_id"
CONF_ID_CONTEXT = "id_context"
CONF_OSCORE_SEQ_THRESHOLD = "oscore_seq_threshold"

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
