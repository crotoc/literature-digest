"""domain/notes。详见 contract.py。"""

from domain.notes.contract import WorkNoteDTO, get_note, list_notes_for_works, set_note

__all__ = [
    "WorkNoteDTO",
    "get_note",
    "list_notes_for_works",
    "set_note",
]
