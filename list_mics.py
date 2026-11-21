#!/usr/bin/env python3
"""Lists input devices: use it after connecting the USB mic."""

from capture import list_input_devices

if __name__ == "__main__":
    print("Available input devices:")
    for line in list_input_devices():
        print("  " + line)
    print(
        "\nTo use it: export RIUNIONI_AUDIO_DEVICE=\"hw:<number>,0\" "
        "before starting server.py"
    )
