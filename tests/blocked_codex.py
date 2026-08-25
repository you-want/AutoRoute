#!/usr/bin/env python3
import sys

sys.stderr.write("Error: failed to initialize in-process app-server: Operation not permitted\n")
raise SystemExit(1)
