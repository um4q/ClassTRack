"""Pure-Python calculation services - no Qt imports, no openpyxl imports.

Every function here takes already-loaded dataclass instances (from
``app.models``) plus primitives, and returns dataclasses/primitives. UI code
fetches rows from ``ExcelStore`` and hands them to these functions; nothing
in this package touches the workbook or the screen.
"""
