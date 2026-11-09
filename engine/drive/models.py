from datetime import datetime

from pydantic import BaseModel, ConfigDict


class DriveFile(BaseModel):
    """A file in a Google Drive folder, as returned by `files.list`."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    mime_type: str
    md5: str | None = None
    size_bytes: int | None = None
    created_at: datetime | None = None
    description: str | None = None
