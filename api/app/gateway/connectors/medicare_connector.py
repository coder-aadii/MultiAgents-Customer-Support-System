"""Deterministic MediCare Telehealth connector for Healthcare entities (patients, appointments, prescriptions)."""

from __future__ import annotations

from typing import Any

from app.gateway.base import (
    BaseConnector,
    CapabilityDefinition,
    EntityNotFoundError,
    EntityRecord,
)


class MediCareConnector(BaseConnector):
    """Connector representing a clinical telehealth backend (MediCare Telehealth).

    Supplies verified patient fixtures for demographics, appointments, and prescriptions.
    Enforces strict patient ownership, HIPAA/data privacy boundaries, and IDOR/BOLA isolation.
    """

    _NAME = "medicare_telehealth"
    _SUPPORTED_TYPES = ["patient", "appointment", "prescription"]

    def __init__(self, custom_records: list[dict[str, Any]] | None = None) -> None:
        self._records_by_key: dict[str, dict[str, Any]] = {}
        self._seed_default_fixtures()
        if custom_records:
            for rec in custom_records:
                key = self._build_key(rec["entity_type"], rec["entity_id"])
                self._records_by_key[key] = rec

    @property
    def connector_name(self) -> str:
        return self._NAME

    @property
    def supported_entity_types(self) -> list[str]:
        return list(self._SUPPORTED_TYPES)

    def _build_key(self, entity_type: str, entity_id: str) -> str:
        return f"{entity_type.strip().lower()}:{entity_id.strip().upper()}"

    def _seed_default_fixtures(self) -> None:
        fixtures: list[dict[str, Any]] = [
            # Marcus Vance (pat_8812)
            {
                "entity_type": "patient",
                "entity_id": "PAT-8812",
                "customer_id": "pat_8812",
                "title": "Patient Record #PAT-8812",
                "summary_fields": {
                    "patient_id": "PAT-8812",
                    "name": "Marcus Vance",
                    "primary_doctor": "Dr. Sarah Lin, MD",
                    "department": "Cardiology",
                    "status": "active",
                },
                "raw_payload": {
                    "patient_id": "PAT-8812",
                    "customer_id": "pat_8812",
                    "dob": "1984-07-22",
                    "clinic": "MediCare Telehealth Chicago",
                },
            },
            {
                "entity_type": "patient",
                "entity_id": "pat_8812",
                "customer_id": "pat_8812",
                "title": "Patient Record #pat_8812",
                "summary_fields": {
                    "patient_id": "pat_8812",
                    "name": "Marcus Vance",
                    "primary_doctor": "Dr. Sarah Lin, MD",
                    "department": "Cardiology",
                    "status": "active",
                },
                "raw_payload": {
                    "patient_id": "pat_8812",
                    "customer_id": "pat_8812",
                },
            },
            {
                "entity_type": "appointment",
                "entity_id": "APT-1092",
                "customer_id": "pat_8812",
                "title": "Clinic Appointment #APT-1092",
                "summary_fields": {
                    "appointment_id": "APT-1092",
                    "doctor": "Dr. Sarah Lin, MD",
                    "datetime": "2026-11-20 10:00 AM",
                    "department": "Cardiology",
                    "status": "confirmed",
                    "location": "Virtual Telehealth Room 4",
                },
                "raw_payload": {
                    "appointment_id": "APT-1092",
                    "customer_id": "pat_8812",
                },
            },
            {
                "entity_type": "prescription",
                "entity_id": "RX-44912",
                "customer_id": "pat_8812",
                "title": "Prescription Refill #RX-44912",
                "summary_fields": {
                    "prescription_id": "RX-44912",
                    "medication": "Lisinopril 10mg",
                    "dosage": "Once daily with water",
                    "refills_remaining": 2,
                    "status": "active",
                    "prescribing_doctor": "Dr. Sarah Lin, MD",
                },
                "raw_payload": {
                    "prescription_id": "RX-44912",
                    "customer_id": "pat_8812",
                    "pharmacy": "Walgreens Downtown",
                },
            },
            # Foreign records for BOLA isolation testing
            {
                "entity_type": "appointment",
                "entity_id": "APT-9999",
                "customer_id": "pat_foreign_999",
                "title": "Foreign Clinic Visit",
                "summary_fields": {
                    "appointment_id": "APT-9999",
                    "status": "confirmed",
                },
                "raw_payload": {"customer_id": "pat_foreign_999"},
            },
        ]
        for fix in fixtures:
            key = self._build_key(fix["entity_type"], fix["entity_id"])
            self._records_by_key[key] = fix

    def get_capabilities(self) -> list[CapabilityDefinition]:
        return [
            CapabilityDefinition(
                name="get_patient_summary",
                description="Get patient profile, primary clinician, and department assignment.",
                entity_type="patient",
                parameters_schema={"type": "object", "properties": {"entity_id": {"type": "string"}}, "required": ["entity_id"]},
            ),
            CapabilityDefinition(
                name="get_appointment_summary",
                description="Get telehealth appointment date, time, clinician, and confirmation status.",
                entity_type="appointment",
                parameters_schema={"type": "object", "properties": {"entity_id": {"type": "string"}}, "required": ["entity_id"]},
            ),
            CapabilityDefinition(
                name="list_customer_appointments",
                description="List all scheduled appointments for the verified patient.",
                entity_type="appointment",
                parameters_schema={"type": "object", "properties": {}},
            ),
            CapabilityDefinition(
                name="get_prescription_summary",
                description="Get prescription medication, dosage schedule, and refills remaining.",
                entity_type="prescription",
                parameters_schema={"type": "object", "properties": {"entity_id": {"type": "string"}}, "required": ["entity_id"]},
            ),
        ]

    async def get_entity_summary(
        self, entity_type: str, entity_id: str, customer_id: str
    ) -> EntityRecord:
        key = self._build_key(entity_type, entity_id)
        rec = self._records_by_key.get(key)
        if not rec:
            raise EntityNotFoundError(f"Entity '{entity_id}' of type '{entity_type}' not found.")
        if rec["customer_id"] != customer_id:
            raise EntityNotFoundError(f"Entity '{entity_id}' does not belong to requesting customer.")

        return EntityRecord(
            entity_type=rec["entity_type"],
            entity_id=rec["entity_id"],
            customer_id=rec["customer_id"],
            title=rec["title"],
            summary_fields=rec["summary_fields"],
            raw_payload=rec["raw_payload"],
        )

    async def list_customer_entities(
        self, entity_type: str, customer_id: str
    ) -> list[EntityRecord]:
        norm_type = entity_type.strip().lower()
        results: list[EntityRecord] = []
        for rec in self._records_by_key.values():
            if rec["entity_type"] == norm_type and rec["customer_id"] == customer_id:
                results.append(
                    EntityRecord(
                        entity_type=rec["entity_type"],
                        entity_id=rec["entity_id"],
                        customer_id=rec["customer_id"],
                        title=rec["title"],
                        summary_fields=rec["summary_fields"],
                        raw_payload=rec["raw_payload"],
                    )
                )
        return results
