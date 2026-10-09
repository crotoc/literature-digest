"""domain/notes 的对外表面。外部只许 import 这里的东西，不碰 models.py。"""

from domain.notes.service import WorkNoteDTO, get_note, list_notes_for_works, set_note

__all__ = [
    "WorkNoteDTO",
    "get_note",
    "list_notes_for_works",
    "set_note",
]
