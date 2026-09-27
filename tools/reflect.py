#!/usr/bin/env python3
"""Run the generated Reflect CLI from a checkout, preserving the project cwd."""
import sys

if sys.version_info < (3, 11):
    sys.exit("LLM Reflect requires Python 3.11 or newer.")

from pathlib import Path
import runpy

sys.dont_write_bytecode = True
scripts = Path(__file__).resolve().parents[1] / "plugins/codex-reflect/scripts"
sys.path.insert(0, str(scripts))
runpy.run_path(str(scripts / "reflect.py"), run_name="__main__")
