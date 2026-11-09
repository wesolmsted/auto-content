from pydantic import BaseModel, ConfigDict, Field


class Notification(BaseModel):
    """A phone notification. `click_url` opens when the notification is tapped."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str = Field(min_length=1)
    body: str
    click_url: str | None = None
    tags: tuple[str, ...] = ()
