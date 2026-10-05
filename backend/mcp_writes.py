"""Explicit content-maintenance tools using the workbench's validated API routes."""
# No postponed annotations: MCP inspects the locally reused API model objects.
from typing import Annotated, Any

from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator

from .language import LanguageEdit
from .release_calendar import CalendarEdit

WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=True,
                        idempotent_hint=False, open_world_hint=False)
ScriptID = Annotated[str, Field(min_length=1, max_length=32)]
WorkID = Annotated[int, Field(strict=True, gt=0)]


class ProfileMaintenance(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: StrictStr | None = Field(default=None, min_length=1, max_length=80)
    bio: StrictStr | None = Field(default=None, max_length=160)
    expected_revision: StrictInt = Field(ge=0)

    @model_validator(mode='after')
    def has_changes(self):
        changed = self.model_fields_set & {'name', 'bio'}
        if not changed or any(getattr(self, field) is None for field in changed):
            raise ValueError('Provide name and/or bio, not null')
        return self


class CalendarMaintenance(CalendarEdit):
    # The tool resolves its top-level identity; edit cannot override that work.
    work_id: None = Field(default=None, exclude=True)
    model_config = ConfigDict(extra='forbid', json_schema_extra=lambda schema: schema['properties'].pop('work_id', None))

    @model_validator(mode='before')
    @classmethod
    def no_internal_work_id(cls, values):
        if isinstance(values, dict) and 'work_id' in values:
            raise ValueError('Supply the identity at tool level, not inside edit')
        return values


def register_content_tools(server, request, work, public_work, models):
    WorkBase = models['work']
    TagCreate = models['tag_create']
    TagEdit = models['tag_edit']
    WorkTags = models['work_tags']
    WorkLinks = models['work_links']

    class WorkMaintenance(WorkBase):
        model_config = ConfigDict(extra='forbid', json_schema_extra=lambda schema: schema['properties'].pop('status', None))
        expected_revision: str = Field(strict=True, pattern=r'^[a-f0-9]{64}$')

        @model_validator(mode='before')
        @classmethod
        def no_legacy_status(cls, values):
            if isinstance(values, dict) and 'status' in values:
                raise ValueError('Maintain es_published and patreon_published independently, not legacy status')
            return values

        @model_validator(mode='after')
        def has_changes(self):
            if not self.model_fields_set - {'expected_revision'}:
                raise ValueError('Provide at least one work field to maintain')
            return self

    @server.tool(annotations=WRITE)
    async def workbench_update_work(edit: WorkMaintenance, script_id: ScriptID | None = None,
                                    work_id: WorkID | None = None) -> dict[str, Any]:
        """Maintain title/notes, ES/Patreon statuses or actual dates on explicit user request.

        First read workbench_get_work and copy data_revision to edit.expected_revision.
        Maintain ES and Patreon statuses independently; legacy combined status is not accepted.
        Records only workbench data; does not publish to any external site or confirm production.
        Dates must be user-confirmed, never inferred from a publishing plan.
        """
        current = await work(script_id, work_id)
        result = await request('PATCH', f'/api/works/{current["id"]}', body=edit.model_dump(exclude_unset=True))
        return public_work(result)

    @server.tool(annotations=WRITE)
    async def workbench_create_tag(edit: TagCreate) -> dict[str, Any]:
        """Create one shared classification tag on explicit user request; never overwrite a duplicate."""
        return await request('POST', '/api/tags', body=edit.model_dump(exclude_unset=True))

    @server.tool(annotations=WRITE)
    async def workbench_update_tag(tag_id: Annotated[int, Field(strict=True, gt=0)], edit: TagEdit) -> dict[str, Any]:
        """Maintain a shared tag name/author support URL using its read revision; affects linked works."""
        return await request('PATCH', f'/api/tags/{tag_id}', body=edit.model_dump(exclude_unset=True))

    @server.tool(annotations=WRITE)
    async def workbench_set_work_tags(edit: WorkTags, script_id: ScriptID | None = None,
                                     work_id: WorkID | None = None) -> dict[str, Any]:
        """Replace a work's COMPLETE classification set using tags_revision as expected_revision.

        An empty list clears all labels. Preserve unrelated current tags unless the user requests removal.
        First read workbench_get_work; do not automatically resolve a 409 conflict.
        """
        current = await work(script_id, work_id)
        return await request('PUT', f'/api/works/{current["id"]}/tags', body=edit.model_dump())

    @server.tool(annotations=WRITE)
    async def workbench_update_work_links(edit: WorkLinks, script_id: ScriptID | None = None,
                                         work_id: WorkID | None = None) -> dict[str, Any]:
        """Maintain selected ES/Patreon/video/script links or actual dates with links_revision.

        Existing UI rules apply: a newly supplied or changed nonempty ES link marks ES published;
        an unchanged link does not change that status. New or changed ES/Patreon links with no actual
        date use today's date unless an explicit date (including null) is supplied.
        Only record user-confirmed publication details; this never publishes externally.
        """
        current = await work(script_id, work_id)
        return await request('PATCH', f'/api/works/{current["id"]}/links', body=edit.model_dump(exclude_unset=True))

    @server.tool(annotations=WRITE)
    async def workbench_update_release_calendar(edit: CalendarMaintenance, script_id: ScriptID | None = None,
                                               work_id: WorkID | None = None) -> dict[str, Any]:
        """Maintain ES/Patreon actual or planned dates using the revision from the read calendar.

        Actual non-null dates mark selected platforms published in workbench; clearing a date
        preserves publication status. Planned dates never become actual automatically.
        Save the returned operation ID for guarded undo. Does not publish externally.
        """
        current = await work(script_id, work_id)
        body = {**edit.model_dump(exclude_unset=True), 'work_id': current['id']}
        return await request('POST', '/api/release-calendar', body=body)

    @server.tool(annotations=WRITE)
    async def workbench_undo_calendar_operation(operation_id: Annotated[int, Field(strict=True, gt=0)]) -> dict[str, Any]:
        """Undo the specified prior calendar operation only if its current revision is still intact.

        User must request undo. A 409 requires reading current calendar and manual reconciliation;
        never retry automatically or replace later edits.
        """
        return await request('POST', f'/api/release-calendar/operations/{operation_id}/undo', body={})

    @server.tool(annotations=WRITE)
    async def workbench_update_profile(edit: ProfileMaintenance) -> dict[str, Any]:
        """Maintain workspace name/bio using the profile revision from workbench_get_settings.

        Existing avatar is preserved; no avatar bytes can be supplied or returned by this tool.
        """
        current = await request('GET', '/api/profile')
        body = {key: current[key] for key in ('name', 'bio', 'avatar')}
        body.update(edit.model_dump(exclude_unset=True))
        result = await request('PUT', '/api/profile', body=body)
        return {key: value for key, value in result.items() if key != 'avatar'}

    @server.tool(annotations=WRITE)
    async def workbench_update_language(edit: LanguageEdit) -> dict[str, Any]:
        """Maintain shared interface language using its settings revision, without translating stored content."""
        return await request('PUT', '/api/settings/language', body=edit.model_dump())
