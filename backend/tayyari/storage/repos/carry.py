"""Carrying a person's things from one profile to the one a re-read of their graph made (`ProfileCarryRepo`).

One session, so one transaction: the resumes, resume files, own-words lines (`user_facts`) and dashboard notes
(`desk_notes`) of the old profile all move, or (on any error) none does. A user fact's primary key is a hash that
includes the profile id, so its key is recomputed for the new profile. Nothing else is touched: applications, matches,
practice sessions and plans stay with the profile they were made for.
"""

from __future__ import annotations

from sqlalchemy import and_, delete, or_, select, update

from ..db import Database, rowcount
from ..ports import CarriedRows
from ..tables import DeskNoteRow, ProfileRow, ResumeFileRow, ResumeRow, UserFactRow
from .match import user_fact_key


class SqlProfileCarryRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def carry(self, old_profile_id: str, new_profile_id: str, user_id: str | None) -> CarriedRows:
        if old_profile_id == new_profile_id:
            raise PermissionError("a profile cannot be carried onto itself")
        async with self._db.session() as s:
            if user_id is not None:
                # The signed-in caller made the new profile: it is theirs from here on (the first write of the transaction).
                await s.execute(
                    update(ProfileRow)
                    .where(
                        ProfileRow.id == new_profile_id,
                        ProfileRow.user_id.is_(None),
                        ProfileRow.is_sample.is_(False),
                    )
                    .values(user_id=user_id)
                )
            found = {
                row.id: row
                for row in (
                    await s.execute(
                        select(ProfileRow.id, ProfileRow.user_id, ProfileRow.is_sample).where(
                            ProfileRow.id.in_([old_profile_id, new_profile_id])
                        )
                    )
                ).all()
            }
            old, new = found.get(old_profile_id), found.get(new_profile_id)
            if (
                old is None
                or new is None
                or old.is_sample
                or new.is_sample
                or old.user_id != user_id
                or new.user_id != user_id
            ):
                raise PermissionError("the caller may not carry these profiles")

            # resumes: the new profile's clashing (kind, role) are replaced by the moved ones; the moved default stays
            # the one default
            resumes = (
                await s.execute(
                    select(ResumeRow.kind, ResumeRow.role_key, ResumeRow.is_default).where(
                        ResumeRow.profile_id == old_profile_id
                    )
                )
            ).all()
            if resumes:
                await s.execute(
                    delete(ResumeRow).where(
                        ResumeRow.profile_id == new_profile_id,
                        or_(
                            *(
                                and_(ResumeRow.kind == r.kind, ResumeRow.role_key == r.role_key)
                                for r in resumes
                            )
                        ),
                    )
                )
                if any(r.is_default for r in resumes):
                    await s.execute(
                        update(ResumeRow)
                        .where(ResumeRow.profile_id == new_profile_id)
                        .values(is_default=False)
                    )
            moved_resumes = rowcount(
                await s.execute(
                    update(ResumeRow)
                    .where(ResumeRow.profile_id == old_profile_id)
                    .values(profile_id=new_profile_id, user_id=user_id)
                )
            )
            moved_files = rowcount(
                await s.execute(
                    update(ResumeFileRow)
                    .where(ResumeFileRow.profile_id == old_profile_id)
                    .values(profile_id=new_profile_id, user_id=user_id)
                )
            )

            # own-words lines: the key is a hash of (profile id, fact id), so each row gets the new profile's key
            moved_facts = 0
            facts = (
                await s.execute(
                    select(UserFactRow.id, UserFactRow.fact).where(UserFactRow.profile_id == old_profile_id)
                )
            ).all()
            for row in facts:
                key = user_fact_key(new_profile_id, str(row.fact.get("id", "")))
                await s.execute(delete(UserFactRow).where(UserFactRow.id == key))
                moved_facts += rowcount(
                    await s.execute(
                        update(UserFactRow)
                        .where(UserFactRow.id == row.id)
                        .values(id=key, profile_id=new_profile_id, user_id=user_id)
                    )
                )

            # dashboard notes (an account's): same note id on the new profile is replaced by the moved one
            moved_notes = 0
            if user_id is not None:
                ids = list(
                    (
                        await s.execute(
                            select(DeskNoteRow.note_id).where(
                                DeskNoteRow.user_id == user_id, DeskNoteRow.profile_id == old_profile_id
                            )
                        )
                    ).scalars()
                )
                if ids:
                    await s.execute(
                        delete(DeskNoteRow).where(
                            DeskNoteRow.user_id == user_id,
                            DeskNoteRow.profile_id == new_profile_id,
                            DeskNoteRow.note_id.in_(ids),
                        )
                    )
                    moved_notes = rowcount(
                        await s.execute(
                            update(DeskNoteRow)
                            .where(DeskNoteRow.user_id == user_id, DeskNoteRow.profile_id == old_profile_id)
                            .values(profile_id=new_profile_id)
                        )
                    )
        return CarriedRows(
            resumes=moved_resumes, resume_files=moved_files, user_facts=moved_facts, desk_notes=moved_notes
        )


__all__ = ["SqlProfileCarryRepo"]
