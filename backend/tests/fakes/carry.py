"""A dict-backed `ProfileCarryRepo` over the other fakes' rows, with the SQL one's rules (the contract test runs both):
all four kinds of row move or none, only for the account that may open the old profile, the new profile's clashing rows
are replaced by the moved ones, and the moved default stays the one default. ``fail_at`` names a stage ("resumes",
"files", "facts", "notes") at which the next call raises after that stage's work, to prove nothing stays half-moved."""

from __future__ import annotations

import asyncio
import copy
from dataclasses import replace
from typing import Any

from tayyari.storage.ports import CarriedRows
from tests.fakes.desk_notes import FakeDeskNoteRepo
from tests.fakes.resume_files import FakeResumeFileRepo
from tests.fakes.resumes import FakeResumeRepo


class FakeProfileCarryRepo:
    def __init__(
        self,
        state: Any,
        resumes: FakeResumeRepo,
        files: FakeResumeFileRepo,
        notes: FakeDeskNoteRepo,
    ) -> None:
        self._s = state
        self._resumes = resumes
        self._files = files
        self._notes = notes
        self.fail_at: str | None = None

    def _stage(self, name: str) -> None:
        if self.fail_at == name:
            self.fail_at = None
            raise RuntimeError(f"carry failed at {name}")

    async def carry(self, old_profile_id: str, new_profile_id: str, user_id: str | None) -> CarriedRows:
        await asyncio.sleep(0)
        if old_profile_id == new_profile_id:
            raise PermissionError("a profile cannot be carried onto itself")
        profiles = self._s.profiles
        old, new = profiles.get(old_profile_id), profiles.get(new_profile_id)
        claim = user_id is not None and new is not None and new.user_id is None and not new.is_sample
        owner_new = user_id if claim else (None if new is None else new.user_id)
        if (
            old is None
            or new is None
            or old.is_sample
            or new.is_sample
            or old.user_id != user_id
            or owner_new != user_id
        ):
            raise PermissionError("the caller may not carry these profiles")

        # work on copies; nothing is written until every stage has succeeded
        resumes = {k: copy.deepcopy(v) for k, v in self._resumes.rows.items()}
        moving = [r for r in resumes.values() if r.profile_id == old_profile_id]
        clashing = {(r.kind, r.role_key) for r in moving}
        for rid in [
            k
            for k, r in resumes.items()
            if r.profile_id == new_profile_id and (r.kind, r.role_key) in clashing
        ]:
            del resumes[rid]
        if any(r.is_default for r in moving):
            for rid, r in list(resumes.items()):
                if r.profile_id == new_profile_id:
                    resumes[rid] = replace(r, is_default=False)
        for r in moving:
            resumes[r.id] = replace(r, profile_id=new_profile_id, user_id=user_id)
        self._stage("resumes")

        files = dict(self._files.rows)
        moved_files = [fid for fid, f in files.items() if f.profile_id == old_profile_id]
        for fid in moved_files:
            files[fid] = replace(files[fid], profile_id=new_profile_id, user_id=user_id)
        self._stage("files")

        facts = dict(self._s.user_facts)
        moved_facts = [k for k in facts if k[0] == old_profile_id]
        for key in moved_facts:
            row = facts.pop(key)
            facts[(new_profile_id, key[1])] = replace(row, profile_id=new_profile_id, user_id=user_id)
        self._stage("facts")

        notes = {k: dict(v) for k, v in self._notes.rows.items()}
        moved_notes = 0
        if user_id is not None:
            mine = notes.pop((user_id, old_profile_id), {})
            if mine:
                target = notes.setdefault((user_id, new_profile_id), {})
                target.update(mine)
                moved_notes = len(mine)
        self._stage("notes")

        if claim:
            new.user_id = user_id
        self._resumes.rows = resumes
        self._files.rows = files
        self._s.user_facts = facts
        self._notes.rows = notes
        return CarriedRows(
            resumes=len(moving),
            resume_files=len(moved_files),
            user_facts=len(moved_facts),
            desk_notes=moved_notes,
        )


__all__ = ["FakeProfileCarryRepo"]
