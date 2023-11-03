from os import getenv

RABBITMQ = {
    "PROTOCOL": getenv(
        "RABBITMQ_DEFAULT_PROTOCOL", "amqp"
    ),  # in prod change with "amqps"
    "HOST": getenv("RABBITMQ_DEFAULT_HOST", "localhost"),
    "VHOST": getenv("RABBITMQ_DEFAULT_VHOST", "/"),
    "PORT": getenv("RABBITMQ_DEFAULT_PORT", 5672),
    "USER": getenv("RABBITMQ_DEFAULT_USER", "guest"),
    "PASSWORD": getenv("RABBITMQ_DEFAULT_PASS", "guest"),
}
