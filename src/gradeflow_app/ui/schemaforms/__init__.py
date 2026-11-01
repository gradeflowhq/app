"""Django forms for the objects the core describes with JSON schemas, knowing nothing of any one of them.

An object's scalar and list fields become form fields; its nested objects become prefixed fields of the same
form; its lists of plain objects become rows; maps of tagged values become name, type and value rows. Checks nested
in it are not fields: the editor shows the core's slots of them as cards.
"""
