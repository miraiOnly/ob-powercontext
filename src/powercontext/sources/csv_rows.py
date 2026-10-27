# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Row-level CSV evidence Sources.

Each physical CSV record becomes one CAPTURED Source whose stable identity is
derived from the document and its primary-key value. Values and column order
are preserved verbatim so a persisted citation can always resolve back to the
exact original row.
"""

from __future__ import annotations

import csv
import io
import json
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, field_validator

from powercontext.sources import AdapterSourceDefinition, TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence
from powercontext.sources.models import Source, SourceMaterialization

CSV_ROW_SOURCE_NAME = "csv-row"
CSV_ROW_SOURCE_VERSION = "1"
CSV_SOURCE_ID_PREFIX = "csv:"
MAX_CSV_SOURCE_ID_LENGTH = 256


def _require_document_id(value: str) -> str:
    if not value or value != value.strip():
        raise ValueError("document_id must be non-empty and contain no leading or trailing whitespace")  # noqa: TRY003
    return value


def _require_primary_key(value: str) -> str:
    if not value:
        raise ValueError("primary_key must be non-empty")  # noqa: TRY003
    return value


def csv_row_source_id(document_id: str, primary_key_value: str) -> str:
    """Build the stable row identity from its two quoting components."""

    _require_document_id(document_id)
    identity = (
        f"{CSV_SOURCE_ID_PREFIX}"
        f"{quote(document_id, safe='')}:"
        f"{quote(primary_key_value, safe='')}"
    )
    if len(identity) > MAX_CSV_SOURCE_ID_LENGTH:
        raise ValueError(f"csv row source_id must not exceed {MAX_CSV_SOURCE_ID_LENGTH} characters")  # noqa: TRY003
    return identity


class CSVDocumentInput(BaseModel):
    """A complete offline CSV document together with its row identity rules."""

    model_config = ConfigDict(frozen=True)

    document_id: str
    primary_key: str
    content: bytes

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str) -> str:
        return _require_document_id(value)

    @field_validator("primary_key")
    @classmethod
    def validate_primary_key(cls, value: str) -> str:
        return _require_primary_key(value)


class CSVRowInput(BaseModel):
    """One parsed CSV record kept verbatim in its original column order."""

    model_config = ConfigDict(frozen=True)

    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]


class CSVRowSource(Source):
    """Captured row evidence identified by document and primary-key value."""

    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]

    @property
    def source_id(self) -> str:
        """Alias of the stable row identity carried by ``name``."""

        return self.name


def parse_csv_document(document: CSVDocumentInput) -> tuple[CSVRowInput, ...]:
    """Parse and validate the whole CSV document before returning any row."""

    _require_document_id(document.document_id)
    _require_primary_key(document.primary_key)
    if not document.content:
        raise ValueError("csv document must contain a header record")  # noqa: TRY003

    try:
        text = document.content.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as error:
        raise ValueError("csv document must be valid UTF-8") from error  # noqa: TRY003

    records: list[list[str]] = []
    try:
        reader = csv.reader(
            io.StringIO(text, newline=""),
            delimiter=",",
            quotechar='"',
            strict=True,
        )
        for record in reader:
            records.append(record)
    except csv.Error as error:
        raise ValueError(f"malformed csv document: {error}") from error  # noqa: TRY003

    if not records or not records[0]:
        raise ValueError("csv document must contain a valid header record")  # noqa: TRY003

    header = records[0]
    for column in header:
        if not column or not column.strip():
            raise ValueError("csv header fields must be non-empty and non-blank")  # noqa: TRY003
    if len(set(header)) != len(header):
        raise ValueError("csv header fields must be unique")  # noqa: TRY003

    try:
        primary_key_index = header.index(document.primary_key)
    except ValueError as error:
        raise ValueError(
            f"primary_key {document.primary_key!r} does not match any header field",
        ) from error  # noqa: TRY003

    rows: list[CSVRowInput] = []
    seen_keys: set[str] = set()
    for record in records[1:]:
        if not record:
            # Completely empty physical record after the header: ignore.
            continue
        if len(record) != len(header):
            raise ValueError(
                f"csv record has {len(record)} fields, expected {len(header)}",
            )  # noqa: TRY003
        primary_key_value = record[primary_key_index]
        if not primary_key_value or not primary_key_value.strip():
            raise ValueError("primary-key value must not be empty or blank")  # noqa: TRY003
        if primary_key_value in seen_keys:
            raise ValueError(
                f"duplicate primary-key value {primary_key_value!r} in document",
            )  # noqa: TRY003
        seen_keys.add(primary_key_value)
        rows.append(
            CSVRowInput(
                document_id=document.document_id,
                primary_key=document.primary_key,
                primary_key_value=primary_key_value,
                columns=tuple(header),
                values=tuple(record),
            )
        )

    return tuple(rows)


class CSVRowSourceAdapter:
    """Resolve and read one CSV row as a captured Source."""

    input_class = CSVRowInput
    name = CSV_ROW_SOURCE_NAME
    source_class = CSVRowSource

    async def resolve(self, value: CSVRowInput, /) -> CSVRowSource:
        identity = csv_row_source_id(value.document_id, value.primary_key_value)
        return CSVRowSource(
            name=identity,
            materialization=SourceMaterialization.CAPTURED,
            document_id=value.document_id,
            primary_key=value.primary_key,
            primary_key_value=value.primary_key_value,
            columns=value.columns,
            values=value.values,
        )

    async def read(self, source: CSVRowSource, /) -> CSVRowInput:
        return CSVRowInput(
            document_id=source.document_id,
            primary_key=source.primary_key,
            primary_key_value=source.primary_key_value,
            columns=source.columns,
            values=source.values,
        )


class CSVRowTextEvidenceProjection:
    """Project one row to the standard text-evidence shape."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = CSVRowSource
    output_class = TextEvidence

    def project(self, source: CSVRowSource, /) -> TextEvidence:
        row_mapping = dict(zip(source.columns, source.values))
        return TextEvidence(
            source_type=CSV_ROW_SOURCE_NAME,
            source_id=source.name,
            content=json.dumps(row_mapping, ensure_ascii=False, separators=(",", ":")),
            metadata={
                "document_id": source.document_id,
                "primary_key": source.primary_key,
                "primary_key_value": source.primary_key_value,
                "columns": list(source.columns),
                "row": row_mapping,
            },
        )


CSV_ROW_SOURCE_ADAPTER = CSVRowSourceAdapter()
CSV_ROW_SOURCE_DEFINITION = AdapterSourceDefinition(
    CSV_ROW_SOURCE_ADAPTER,
    version=CSV_ROW_SOURCE_VERSION,
    projections=(CSVRowTextEvidenceProjection(),),
)

__all__ = [
    "CSVDocumentInput",
    "CSVRowInput",
    "CSVRowSource",
    "CSV_ROW_SOURCE_DEFINITION",
    "CSV_ROW_SOURCE_NAME",
    "CSV_ROW_SOURCE_VERSION",
    "csv_row_source_id",
    "parse_csv_document",
]
