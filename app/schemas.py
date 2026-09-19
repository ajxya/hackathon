"""EDFlow request schemas — define what JSON the event endpoints accept.

Every field is optional, so you can call these endpoints with an empty
body (`{}`) and still get a fully valid, auto-generated patient.
"""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class CheckInRequest(BaseModel):
    name: Optional[str] = Field(default=None, description="Leave blank to auto-generate a synthetic name")
    acuity: Optional[int] = Field(
        default=None, ge=1, le=5, description="Priority tier: 1 (most severe) to 5 (least severe). Leave blank to randomize."
    )
    injury: Optional[str] = Field(default=None, description="Leave blank to auto-generate one matching the tier")


class AmbulanceRequest(BaseModel):
    name: Optional[str] = Field(default=None, description="Leave blank to auto-generate a synthetic name")
    acuity: Optional[int] = Field(
        default=None, ge=1, le=5, description="Priority tier: 1 (most severe) to 5 (least severe). Leave blank to randomize."
    )
    injury: Optional[str] = Field(default=None, description="Leave blank to auto-generate one matching the tier")


class AssistantMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=2000)


class AssistantRequest(BaseModel):
    message: str = Field(max_length=1000)
    # The server only ever uses the last 6 anyway (see app/assistant.py);
    # capping the accepted list itself stops an oversized payload from
    # being parsed at all.
    history: List[AssistantMessage] = Field(default_factory=list, max_length=20)
