"""
Debug monitor to catch accidental print() statements.

CRITICAL: This is a development tool to ensure no console output leaks.
Only enable in debug mode.
"""

import sys
import builtins
import traceback
from typing import Optional, Any


_original_print = builtins.print
_print_monitor_enabled = False
_allowed_callers = {
    'app/ui.py',  # Legacy plain UI (only when UI v2 is not active)
}


def _monitored_print(*args, **kwargs):
    """
    Monitored print function that warns about unexpected console output.
    
    In debug mode, this catches stray print() statements and logs them.
    """
    if not _print_monitor_enabled:
        # Not monitoring, use original print
        return _original_print(*args, **kwargs)
    
    # Get caller info
    caller_frame = sys._getframe(1)
    caller_file = caller_frame.f_code.co_filename
    caller_line = caller_frame.f_lineno
    caller_func = caller_frame.f_code.co_name
    
    # Normalize path
    caller_file_normalized = caller_file.replace('\\', '/')
    
    # Check if caller is allowed
    is_allowed = any(allowed in caller_file_normalized for allowed in _allowed_callers)
    
    if not is_allowed:
        # UNEXPECTED PRINT - log to file
        try:
            from .logger import get_logger
            logger = get_logger()
            logger.warning(
                f"[PRINT MONITOR] Unexpected print() call | "
                f"file={caller_file} | line={caller_line} | func={caller_func} | "
                f"args={args[:2]}"  # Show first 2 args only
            )
        except Exception:
            pass  # Silently fail to prevent logging loops
    
    # Still allow the print (but it's redirected by Rich anyway)
    return _original_print(*args, **kwargs)


def enable_print_monitor():
    """
    Enable print() monitoring for debug mode.
    
    This helps catch accidental print() statements that would leak to console.
    """
    global _print_monitor_enabled
    
    if _print_monitor_enabled:
        return  # Already enabled
    
    _print_monitor_enabled = True
    builtins.print = _monitored_print
    
    try:
        from .logger import get_logger
        logger = get_logger()
        logger.debug("[DEBUG] Print monitor enabled - will catch stray print() calls")
    except Exception:
        pass


def disable_print_monitor():
    """Disable print() monitoring."""
    global _print_monitor_enabled
    
    _print_monitor_enabled = False
    builtins.print = _original_print
    
    try:
        from .logger import get_logger
        logger = get_logger()
        logger.debug("[DEBUG] Print monitor disabled")
    except Exception:
        pass


def add_allowed_caller(file_pattern: str):
    """
    Add a file pattern to the allowed callers list.
    
    Args:
        file_pattern: File path pattern (e.g., 'app/ui.py')
    """
    _allowed_callers.add(file_pattern)

