"""Core functionality for the Swim Tracker application."""

from .parser import SwimResult, parse_cl2_file, parse_cl2_text

__all__ = ["SwimResult", "parse_cl2_file", "parse_cl2_text"]
