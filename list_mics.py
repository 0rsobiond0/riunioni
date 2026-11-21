#!/usr/bin/env python3
"""Elenca i dispositivi di ingresso: usalo dopo aver collegato il mic USB."""

from capture import list_input_devices

if __name__ == "__main__":
    print("Dispositivi di ingresso disponibili:")
    for line in list_input_devices():
        print("  " + line)
    print(
        "\nPer usarlo: export RIUNIONI_AUDIO_DEVICE=\"hw:<numero>,0\" "
        "prima di avviare server.py"
    )
