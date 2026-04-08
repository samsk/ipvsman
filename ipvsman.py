#!/usr/bin/env python3
"""Launcher for ipvsman zipapp."""

import os
import sys

ZIP_PATH = "/usr/local/lib/ipvsman.zip"

if not os.path.isfile(ZIP_PATH):
    sys.exit(f"ipvsman: zip not found: {ZIP_PATH}")

os.execv(sys.executable, [sys.executable, ZIP_PATH] + sys.argv[1:])
