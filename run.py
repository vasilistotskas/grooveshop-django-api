from os import getenv

import uvicorn


if __name__ == "__main__":
    SYSTEM_ENV = getenv("SYSTEM_ENV", "dev")

    is_local = SYSTEM_ENV in ["dev", "docker"]
    port = 8000

    uvicorn.run(
        "asgi:application",
        host="0.0.0.0",
        port=port,
        workers=1,
        reload=is_local,
        reload_dirs=[
            ".",
        ],
        log_level="debug",
        log_config={
            "version": 1,
            "formatters": {
                "generic": {
                    "format": "[%(asctime)s - %(name)s - %(lineno)3d][%(levelname)s] %(message)s",  # noqa: E501
                },
            },
        },
        access_log=False,
    )
