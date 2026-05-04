"""Path validation domain models."""

from __future__ import annotations

from pydantic import BaseModel, Field

from react_agent.domain.intake import PathCandidate


class PathValidationResult(BaseModel):
    """Result of validating AI-extracted path candidates against the filesystem."""

    authorized_paths: list[PathCandidate] = Field(default_factory=list)
    source_files: list[PathCandidate] = Field(default_factory=list)
    invalid_paths: list[str] = Field(default_factory=list)
