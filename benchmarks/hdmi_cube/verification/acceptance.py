"""Operator-owned acceptance entrypoint; workers may not edit verification/."""
from pathlib import Path
from fpga_agent.video import validate_project

if __name__ == '__main__':
    validate_project(Path(__file__).resolve().parents[1], full=True)
