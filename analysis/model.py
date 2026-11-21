"""数据模型与 CSV 解析。

约定（与 README 一致）：空字段表示资料未提供，不代表数值为零或业务状态已否定。
"""
from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path


def parse_date(raw: str) -> date | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    return date.fromisoformat(raw)


def parse_text(raw: str) -> str:
    return (raw or "").strip()


@dataclass(frozen=True)
class Participant:
    ref: str
    service_ref: str
    enrolled_at: date | None
    referral_type: str
    baseline_band: str
    participation_state: str


@dataclass(frozen=True)
class Measure:
    ref: str
    instrument_ref: str
    version: str
    construct: str
    valid_from: date | None
    valid_to: date | None

    def in_window(self, when: date | None) -> bool | None:
        """观测日期是否落在量表有效期窗内；日期缺失时返回 None（未知）。"""
        if when is None:
            return None
        if self.valid_from and when < self.valid_from:
            return False
        if self.valid_to and when > self.valid_to:
            return False
        return True


@dataclass(frozen=True)
class Observation:
    ref: str
    participant_ref: str
    measure_ref: str
    observed_at: date | None
    raw_value: str
    response_state: str
    supersedes: str


@dataclass(frozen=True)
class Followup:
    ref: str
    participant_ref: str
    due_at: date | None
    completed_at: date | None
    state: str
    reason_code: str


@dataclass(frozen=True)
class ServiceRecord:
    service_ref: str
    service_type: str
    valid_from: date | None
    valid_to: date | None
    description: str

    def covers(self, when: date | None) -> bool | None:
        if when is None:
            return None
        if self.valid_from and when < self.valid_from:
            return False
        if self.valid_to and when > self.valid_to:
            return False
        return True


@dataclass
class Dataset:
    data_dir: str
    participants: list[Participant]
    measures: list[Measure]
    observations: list[Observation]
    followups: list[Followup]
    services: list[ServiceRecord]
    file_hashes: dict[str, str] = field(default_factory=dict)


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_dataset(data_dir: str | Path) -> Dataset:
    """读取数据目录下的五个 CSV 文件，返回数据集及逐文件 SHA-256。"""
    root = Path(data_dir)
    files = {
        "participants": root / "participants.csv",
        "measures": root / "measures.csv",
        "observations": root / "observations.csv",
        "followups": root / "followups.csv",
        "services": root / "services.csv",
    }
    hashes = {name: _sha256(path) for name, path in files.items()}

    participants = [
        Participant(
            ref=parse_text(r["participant_ref"]),
            service_ref=parse_text(r["service_ref"]),
            enrolled_at=parse_date(r["enrolled_at"]),
            referral_type=parse_text(r["referral_type"]),
            baseline_band=parse_text(r["baseline_band"]),
            participation_state=parse_text(r["participation_state"]),
        )
        for r in _read_rows(files["participants"])
    ]
    measures = [
        Measure(
            ref=parse_text(r["measure_ref"]),
            instrument_ref=parse_text(r["instrument_ref"]),
            version=parse_text(r["version"]),
            construct=parse_text(r["construct"]),
            valid_from=parse_date(r["valid_from"]),
            valid_to=parse_date(r["valid_to"]),
        )
        for r in _read_rows(files["measures"])
    ]
    observations = [
        Observation(
            ref=parse_text(r["observation_ref"]),
            participant_ref=parse_text(r["participant_ref"]),
            measure_ref=parse_text(r["measure_ref"]),
            observed_at=parse_date(r["observed_at"]),
            raw_value=parse_text(r["value"]),
            response_state=parse_text(r["response_state"]),
            supersedes=parse_text(r["supersedes"]),
        )
        for r in _read_rows(files["observations"])
    ]
    followups = [
        Followup(
            ref=parse_text(r["followup_ref"]),
            participant_ref=parse_text(r["participant_ref"]),
            due_at=parse_date(r["due_at"]),
            completed_at=parse_date(r["completed_at"]),
            state=parse_text(r["state"]),
            reason_code=parse_text(r["reason_code"]),
        )
        for r in _read_rows(files["followups"])
    ]
    services = [
        ServiceRecord(
            service_ref=parse_text(r["service_ref"]),
            service_type=parse_text(r["service_type"]),
            valid_from=parse_date(r["valid_from"]),
            valid_to=parse_date(r["valid_to"]),
            description=parse_text(r["description"]),
        )
        for r in _read_rows(files["services"])
    ]
    return Dataset(
        data_dir=str(root),
        participants=participants,
        measures=measures,
        observations=observations,
        followups=followups,
        services=services,
        file_hashes=hashes,
    )
