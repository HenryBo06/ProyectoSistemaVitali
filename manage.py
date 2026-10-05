#!/usr/bin/env python
"""Local management entry point for SmartOrder AI."""

import os
import sys


if __name__ == "__main__":
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "vitali_web.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)
