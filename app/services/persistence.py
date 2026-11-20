"""Select exactly one configured persistence backend for the application process."""

from config import settings

if settings.DATABASE_BACKEND == "postgres":
    from app.services import postgres_ledger as ledger
elif settings.DATABASE_BACKEND == "firestore":
    from app.services import ledger
else:
    raise ValueError("DATABASE_BACKEND must be firestore or postgres")


def confirmation_adapter():
    if settings.DATABASE_BACKEND == "postgres":
        from app.services.postgres_confirmation_adapter import PostgresLedgerAdapter

        return PostgresLedgerAdapter()
    from app.services.confirmation_ledger_adapter import ClaudeLedgerAdapter

    return ClaudeLedgerAdapter()


def invoice_repository():
    if settings.DATABASE_BACKEND == "postgres":
        from pdf_service.postgres_repository import PostgresInvoiceRepository

        return PostgresInvoiceRepository()
    from pdf_service.firestore_repository import FirestoreInvoiceRepository

    return FirestoreInvoiceRepository()


def conversation_store(db, merchant_id):
    if settings.DATABASE_BACKEND == "postgres":
        from app.services.postgres_conversation import PostgresConversationStore

        return PostgresConversationStore(db, merchant_id)
    from app.services.whatsapp_conversation import ConversationStore

    return ConversationStore(db, merchant_id)


def get_invoice_artifact(merchant_id, transaction_id):
    if settings.DATABASE_BACKEND == "postgres":
        from pdf_service.postgres_repository import (
            get_invoice_artifact as read_artifact,
        )

        return read_artifact(merchant_id, transaction_id)
    from pdf_service import InvoiceArtifact

    snap = (
        ledger.get_db()
        .collection("merchants")
        .document(merchant_id)
        .collection("transactions")
        .document(transaction_id)
        .collection("invoice_artifacts")
        .document("basic_v1")
        .get()
    )
    return InvoiceArtifact.model_validate(snap.to_dict()) if snap.exists else None
