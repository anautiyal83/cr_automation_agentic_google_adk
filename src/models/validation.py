"""Document upload validation — checks file existence and format before pipeline start."""
from __future__ import annotations

from pathlib import Path

from src.models.domain import ValidationError, ValidationResult


async def validate_documents(
    uc_name: str,
    ciq_path: str,
    mop_path: str,
    constraints_path: str | None = None,
    command_outputs_path: str | None = None,
) -> ValidationResult:
    """Validate uploaded documents — file existence and format checks.

    Content-level analysis is deferred to pipeline agents (Steps 1-2).
    """
    errors: list[ValidationError] = []

    # CIQ Excel — required
    if not ciq_path or not Path(ciq_path).exists():
        errors.append(ValidationError(
            document="ciq", error_type="missing_file",
            message="CIQ Excel file not found",
        ))
    else:
        suffix = Path(ciq_path).suffix.lower()
        if suffix not in (".xlsx", ".xls"):
            errors.append(ValidationError(
                document="ciq", error_type="invalid_format",
                message=f"CIQ must be .xlsx or .xls, got {suffix}",
            ))

    # MOP — required
    if not mop_path or not Path(mop_path).exists():
        errors.append(ValidationError(
            document="mop", error_type="missing_file",
            message="MOP document not found",
        ))
    else:
        suffix = Path(mop_path).suffix.lower()
        if suffix not in (".docx", ".pdf", ".txt"):
            errors.append(ValidationError(
                document="mop", error_type="invalid_format",
                message=f"MOP must be .docx, .pdf, or .txt, got {suffix}",
            ))

    # Constraints — optional
    if constraints_path and Path(constraints_path).exists():
        suffix = Path(constraints_path).suffix.lower()
        if suffix not in (".txt", ".yaml", ".yml", ".json", ".csv"):
            errors.append(ValidationError(
                document="constraints", error_type="invalid_format",
                message=f"Constraints file format not supported: {suffix}",
            ))

    # Command outputs — optional
    if command_outputs_path and not Path(command_outputs_path).exists():
        errors.append(ValidationError(
            document="command_outputs", error_type="missing_file",
            message="Command outputs file not found",
        ))

    return ValidationResult(valid=len(errors) == 0, errors=errors)
