-- Clandexis phone-based MVP, PostgreSQL 17+, fresh-install schema.
-- Apply once to an EMPTY database with psql -v ON_ERROR_STOP=1 -f postgres_schema.sql.
-- Schema installation only. The current application still uses Firestore until its
-- persistence adapter is migrated. Never enable both databases for ledger writes.
-- Use a dedicated migration owner. The application role must not own these tables,
-- bypass RLS, disable triggers, or have TRUNCATE/DDL privileges.
-- Every application transaction must SET LOCAL app.merchant_id to the UUID resolved
-- from the authenticated approved phone/session. Never accept this value from JSON.
-- Transport HMAC/session verification and Schema 1.1 deterministic validation stay
-- in the application. DB constraints supplement those checks.

BEGIN;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE merchants (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    legacy_firestore_id text UNIQUE,
    phone_e164 text NOT NULL UNIQUE CHECK (phone_e164 ~ '^\+[1-9][0-9]{7,14}$'),
    is_approved boolean NOT NULL DEFAULT false,
    business_name text NOT NULL CHECK (btrim(business_name) <> ''),
    owner_name text,
    owner_email text,
    preferred_language text NOT NULL DEFAULT 'en',
    script_preference text NOT NULL DEFAULT 'Latin',
    currency text NOT NULL DEFAULT 'INR' CHECK (currency ~ '^[A-Z]{3}$'),
    stock_policy text NOT NULL DEFAULT 'BLOCK_NEGATIVE'
        CHECK (stock_policy IN ('BLOCK_NEGATIVE', 'WARN_ALLOW')),
    invoice_number_prefix text NOT NULL DEFAULT 'INV',
    next_invoice_number bigint NOT NULL DEFAULT 1 CHECK (next_invoice_number > 0),
    tax_registration_number text,
    tax_address jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (id, phone_e164)
);

CREATE TABLE parties (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id uuid NOT NULL REFERENCES merchants(id),
    legacy_firestore_id text,
    display_name text NOT NULL CHECK (btrim(display_name) <> ''),
    party_type text NOT NULL DEFAULT 'CUSTOMER' CHECK (party_type IN ('CUSTOMER','SUPPLIER','BOTH','OTHER')),
    phone_e164 text CHECK (phone_e164 ~ '^\+[1-9][0-9]{7,14}$'),
    aliases text[] NOT NULL DEFAULT '{}',
    credit_limit numeric(18,2) CHECK (credit_limit >= 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (merchant_id, id),
    UNIQUE (merchant_id, legacy_firestore_id)
);

CREATE TABLE products (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id uuid NOT NULL REFERENCES merchants(id),
    legacy_firestore_id text,
    sku_code text NOT NULL,
    product_name text NOT NULL CHECK (btrim(product_name) <> ''),
    aliases text[] NOT NULL DEFAULT '{}',
    unit text NOT NULL CHECK (btrim(unit) <> ''),
    default_unit_price numeric(18,2) CHECK (default_unit_price >= 0),
    reorder_level numeric(18,3) NOT NULL DEFAULT 0 CHECK (reorder_level >= 0),
    is_active boolean NOT NULL DEFAULT true,
    UNIQUE (merchant_id, id),
    UNIQUE (merchant_id, sku_code),
    UNIQUE (merchant_id, legacy_firestore_id)
);

-- All channels share non-null source IDs, so retry uniqueness also covers dashboard.
-- Store only this sender's source payload when a webhook contains multiple senders.
CREATE TABLE workflow_messages (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id uuid NOT NULL REFERENCES merchants(id),
    sender_phone text NOT NULL,
    source_channel text NOT NULL CHECK (source_channel IN ('WHATSAPP','DASHBOARD','ADMIN')),
    external_message_id text NOT NULL CHECK (btrim(external_message_id) <> ''),
    message_type text NOT NULL,
    message_text text,
    source_payload jsonb NOT NULL DEFAULT '{}',
    media_reference text,
    received_at timestamptz NOT NULL DEFAULT now(),
    source_timestamp timestamptz,
    processing_status text NOT NULL DEFAULT 'RECEIVED'
        CHECK (processing_status IN ('RECEIVED','PROCESSING','DEFERRED','DONE','FAILED')),
    response_payload jsonb,
    processed_at timestamptz,
    FOREIGN KEY (merchant_id, sender_phone) REFERENCES merchants(id, phone_e164),
    UNIQUE (merchant_id, id),
    UNIQUE (merchant_id, source_channel, external_message_id)
);

CREATE TABLE transaction_candidates (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id uuid NOT NULL REFERENCES merchants(id),
    legacy_firestore_id text,
    source_message_id uuid NOT NULL,
    candidate_index integer NOT NULL DEFAULT 0 CHECK (candidate_index >= 0),
    original_extraction jsonb NOT NULL,
    extracted_snapshot jsonb NOT NULL,
    snapshot_sha256 text GENERATED ALWAYS AS
        (encode(digest(extracted_snapshot::text, 'sha256'), 'hex')) STORED,
    revision bigint NOT NULL DEFAULT 1 CHECK (revision > 0),
    validation_status text GENERATED ALWAYS AS (extracted_snapshot->>'validation_status') STORED,
    posting_status text NOT NULL DEFAULT 'NEEDS_REVIEW'
        CHECK (posting_status IN ('NEEDS_REVIEW','PENDING_CONFIRMATION','POSTED','REJECTED','CANCELLED','EXPIRED')),
    originally_needs_review boolean NOT NULL DEFAULT true,
    review_resolved boolean NOT NULL DEFAULT false,
    latest_review_note text,
    reviewed_by_phone text,
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (merchant_id, source_message_id) REFERENCES workflow_messages(merchant_id,id),
    FOREIGN KEY (merchant_id, reviewed_by_phone) REFERENCES merchants(id,phone_e164),
    UNIQUE (merchant_id,id),
    UNIQUE (merchant_id,source_message_id,candidate_index),
    UNIQUE (merchant_id,legacy_firestore_id),
    CHECK (validation_status IS NOT NULL AND validation_status IN ('ACCEPTED','NEEDS_REVIEW','REJECTED')),
    CHECK (extracted_snapshot->>'schema_version' IS NOT DISTINCT FROM '1.1'),
    CHECK (extracted_snapshot->>'transaction_type' IS NOT NULL),
    CHECK (jsonb_typeof(extracted_snapshot->'missing_fields') IS NOT DISTINCT FROM 'array'),
    CHECK (expires_at > created_at)
);

CREATE TABLE candidate_reviews (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id uuid NOT NULL REFERENCES merchants(id),
    candidate_id uuid NOT NULL,
    from_revision bigint NOT NULL,
    to_revision bigint NOT NULL,
    previous_snapshot jsonb NOT NULL,
    corrected_snapshot jsonb NOT NULL,
    reviewed_by_phone text NOT NULL,
    review_note text NOT NULL CHECK (btrim(review_note) <> ''),
    reviewed_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (merchant_id,candidate_id) REFERENCES transaction_candidates(merchant_id,id),
    FOREIGN KEY (merchant_id,reviewed_by_phone) REFERENCES merchants(id,phone_e164),
    UNIQUE (merchant_id,candidate_id,to_revision),
    CHECK (to_revision = from_revision + 1)
);

CREATE TABLE conversations (
    merchant_id uuid PRIMARY KEY REFERENCES merchants(id),
    sender_phone text NOT NULL UNIQUE,
    active_candidate_id uuid,
    candidate_revision bigint,
    latest_question text,
    missing_fields jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(missing_fields) = 'array'),
    detected_language text NOT NULL DEFAULT 'en',
    script_preference text NOT NULL DEFAULT 'Latin',
    expires_at timestamptz,
    lease_token uuid,
    lease_until timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (merchant_id,sender_phone) REFERENCES merchants(id,phone_e164),
    FOREIGN KEY (merchant_id,active_candidate_id) REFERENCES transaction_candidates(merchant_id,id),
    CHECK ((active_candidate_id IS NULL AND candidate_revision IS NULL AND expires_at IS NULL)
        OR (active_candidate_id IS NOT NULL AND candidate_revision > 0 AND expires_at IS NOT NULL))
);

-- A single terminal decision per candidate. Duplicate callbacks replay this row.
CREATE TABLE confirmations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id uuid NOT NULL REFERENCES merchants(id),
    candidate_id uuid NOT NULL,
    source_message_id uuid NOT NULL,
    request_id text NOT NULL CHECK (btrim(request_id) <> ''),
    action text NOT NULL CHECK (action IN ('CONFIRM','CANCEL')),
    actor_phone text NOT NULL,
    candidate_revision bigint NOT NULL,
    expected_snapshot_sha256 text NOT NULL CHECK (expected_snapshot_sha256 ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (merchant_id,candidate_id) REFERENCES transaction_candidates(merchant_id,id),
    FOREIGN KEY (merchant_id,source_message_id) REFERENCES workflow_messages(merchant_id,id),
    FOREIGN KEY (merchant_id,actor_phone) REFERENCES merchants(id,phone_e164),
    UNIQUE (merchant_id,id),
    UNIQUE (merchant_id,candidate_id),
    UNIQUE (merchant_id,request_id),
    UNIQUE (merchant_id,source_message_id)
);

-- Only committed records live here. Draft/review/payment states stay separate.
-- Internal OPENING_BALANCE/REVERSAL operations also need an approved explicit decision.
CREATE TABLE transactions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id uuid NOT NULL REFERENCES merchants(id),
    legacy_firestore_id text,
    candidate_id uuid NOT NULL,
    confirmation_id uuid NOT NULL,
    intent text NOT NULL CHECK (intent IN ('SALE_INVOICE','PURCHASE_ENTRY','UDHAAR_COLLECTION',
        'EXPENSE','PRODUCTION_LOG','OPENING_BALANCE','REVERSAL')),
    party_id uuid,
    posting_status text NOT NULL DEFAULT 'COMMITTED' CHECK (posting_status = 'COMMITTED'),
    business_date date NOT NULL,
    currency text NOT NULL DEFAULT 'INR' CHECK (currency ~ '^[A-Z]{3}$'),
    subtotal numeric(18,2) NOT NULL DEFAULT 0 CHECK (subtotal >= 0),
    tax_amount numeric(18,2) NOT NULL DEFAULT 0 CHECK (tax_amount >= 0),
    discount_amount numeric(18,2) NOT NULL DEFAULT 0 CHECK (discount_amount >= 0),
    total_amount numeric(18,2) GENERATED ALWAYS AS (subtotal + tax_amount - discount_amount) STORED,
    amount_paid numeric(18,2) NOT NULL DEFAULT 0 CHECK (amount_paid >= 0),
    credit_amount numeric(18,2) NOT NULL DEFAULT 0 CHECK (credit_amount >= 0),
    payment_status text NOT NULL CHECK (payment_status IN ('PAID','PARTIAL','UNPAID','NOT_APPLICABLE')),
    due_date date,
    reversal_of_id uuid,
    committed_at timestamptz NOT NULL DEFAULT now(),
    created_in_xid xid8 NOT NULL DEFAULT pg_current_xact_id(),
    FOREIGN KEY (merchant_id,candidate_id) REFERENCES transaction_candidates(merchant_id,id),
    FOREIGN KEY (merchant_id,confirmation_id) REFERENCES confirmations(merchant_id,id),
    FOREIGN KEY (merchant_id,party_id) REFERENCES parties(merchant_id,id),
    UNIQUE (merchant_id,id),
    UNIQUE (merchant_id,candidate_id),
    UNIQUE (merchant_id,confirmation_id),
    UNIQUE (merchant_id,legacy_firestore_id),
    FOREIGN KEY (merchant_id,reversal_of_id) REFERENCES transactions(merchant_id,id),
    CHECK (discount_amount <= subtotal + tax_amount),
    CHECK (intent NOT IN ('SALE_INVOICE','PURCHASE_ENTRY','EXPENSE','UDHAAR_COLLECTION')
        OR amount_paid + credit_amount = subtotal + tax_amount - discount_amount),
    CHECK (intent <> 'UDHAAR_COLLECTION' OR (party_id IS NOT NULL AND credit_amount = 0 AND amount_paid > 0)),
    CHECK (credit_amount = 0 OR party_id IS NOT NULL),
    CHECK ((intent = 'REVERSAL') = (reversal_of_id IS NOT NULL)),
    CHECK (payment_status = CASE WHEN intent IN ('SALE_INVOICE','PURCHASE_ENTRY','EXPENSE','UDHAAR_COLLECTION')
        THEN CASE WHEN credit_amount=0 THEN 'PAID' WHEN amount_paid=0 THEN 'UNPAID' ELSE 'PARTIAL' END
        ELSE 'NOT_APPLICABLE' END)
);

CREATE TABLE transaction_items (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id uuid NOT NULL REFERENCES merchants(id),
    transaction_id uuid NOT NULL,
    product_id uuid,
    item_description text NOT NULL,
    quantity numeric(18,3) NOT NULL CHECK (quantity > 0),
    unit text NOT NULL CHECK (btrim(unit) <> ''),
    unit_price numeric(18,2) NOT NULL CHECK (unit_price >= 0),
    line_total numeric(18,2) GENERATED ALWAYS AS (round(quantity * unit_price,2)) STORED,
    FOREIGN KEY (merchant_id,transaction_id) REFERENCES transactions(merchant_id,id),
    FOREIGN KEY (merchant_id,product_id) REFERENCES products(merchant_id,id)
);

-- Credit is a receivable/payable entry, never a cash collection or payment method.
CREATE TABLE payments (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id uuid NOT NULL REFERENCES merchants(id),
    transaction_id uuid NOT NULL,
    party_id uuid,
    direction text NOT NULL CHECK (direction IN ('RECEIVED','PAID')),
    method text NOT NULL CHECK (method IN ('CASH','UPI','BANK_TRANSFER','CARD','ADVANCE_ADJUSTED','OTHER')),
    amount numeric(18,2) NOT NULL CHECK (amount > 0),
    currency text NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    reference_number text,
    paid_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (merchant_id,transaction_id) REFERENCES transactions(merchant_id,id),
    FOREIGN KEY (merchant_id,party_id) REFERENCES parties(merchant_id,id)
);

-- Append-only source of party balances. Repayment adds a negative receivable delta.
CREATE TABLE party_balance_movements (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id uuid NOT NULL REFERENCES merchants(id),
    transaction_id uuid NOT NULL,
    party_id uuid NOT NULL,
    balance_type text NOT NULL CHECK (balance_type IN ('RECEIVABLE','PAYABLE')),
    amount_delta numeric(18,2) NOT NULL CHECK (amount_delta <> 0),
    FOREIGN KEY (merchant_id,transaction_id) REFERENCES transactions(merchant_id,id),
    FOREIGN KEY (merchant_id,party_id) REFERENCES parties(merchant_id,id),
    UNIQUE (merchant_id,transaction_id,party_id,balance_type)
);

CREATE TABLE inventory_movements (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id uuid NOT NULL REFERENCES merchants(id),
    transaction_id uuid NOT NULL,
    product_id uuid NOT NULL,
    movement_type text NOT NULL CHECK (movement_type IN ('OPENING_STOCK','PRODUCTION_ADD','SALE_DEDUCT',
        'PURCHASE_ADD','RETURN_ADD','ADJUSTMENT')),
    quantity_delta numeric(18,3) NOT NULL CHECK (quantity_delta <> 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (merchant_id,transaction_id) REFERENCES transactions(merchant_id,id),
    FOREIGN KEY (merchant_id,product_id) REFERENCES products(merchant_id,id),
    UNIQUE (merchant_id,transaction_id,product_id),
    CHECK ((movement_type <> 'SALE_DEDUCT' OR quantity_delta < 0)
        AND (movement_type NOT IN ('PRODUCTION_ADD','PURCHASE_ADD','RETURN_ADD') OR quantity_delta > 0))
);

CREATE TABLE journal_entries (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id uuid NOT NULL REFERENCES merchants(id),
    transaction_id uuid NOT NULL,
    description text NOT NULL,
    FOREIGN KEY (merchant_id,transaction_id) REFERENCES transactions(merchant_id,id),
    UNIQUE (merchant_id,id),
    UNIQUE (merchant_id,transaction_id)
);

CREATE TABLE journal_lines (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id uuid NOT NULL REFERENCES merchants(id),
    journal_entry_id uuid NOT NULL,
    account_code text NOT NULL,
    account_name text NOT NULL,
    debit numeric(18,2) NOT NULL DEFAULT 0 CHECK (debit >= 0),
    credit numeric(18,2) NOT NULL DEFAULT 0 CHECK (credit >= 0),
    FOREIGN KEY (merchant_id,journal_entry_id) REFERENCES journal_entries(merchant_id,id),
    CHECK ((debit > 0 AND credit = 0) OR (credit > 0 AND debit = 0))
);

-- Financial facts are frozen at posting. Only delivery/artifact rows are mutable.
CREATE TABLE invoices (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id uuid NOT NULL REFERENCES merchants(id),
    transaction_id uuid NOT NULL,
    invoice_number text NOT NULL,
    invoice_date date NOT NULL,
    currency text NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    merchant_snapshot jsonb NOT NULL,
    buyer_snapshot jsonb NOT NULL,
    items_snapshot jsonb NOT NULL CHECK (jsonb_typeof(items_snapshot) = 'array'),
    invoice_kind text NOT NULL DEFAULT 'BASIC' CHECK (invoice_kind IN ('BASIC','TAX')),
    tax_registration_number text,
    tax_rule_version text,
    subtotal numeric(18,2) NOT NULL,
    tax_amount numeric(18,2) NOT NULL,
    discount_amount numeric(18,2) NOT NULL,
    total_amount numeric(18,2) NOT NULL,
    amount_paid numeric(18,2) NOT NULL,
    amount_due numeric(18,2) NOT NULL,
    issued_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (merchant_id,transaction_id) REFERENCES transactions(merchant_id,id),
    UNIQUE (merchant_id,id),
    UNIQUE (merchant_id,transaction_id),
    UNIQUE (merchant_id,invoice_number),
    CHECK (invoice_kind <> 'TAX' OR (btrim(tax_registration_number) <> '' AND tax_registration_number IS NOT NULL
        AND tax_rule_version IS NOT NULL AND btrim(tax_rule_version) <> ''))
);

CREATE TABLE invoice_artifacts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id uuid NOT NULL REFERENCES merchants(id),
    invoice_id uuid NOT NULL,
    template_version text NOT NULL,
    generation_status text NOT NULL DEFAULT 'PENDING' CHECK (generation_status IN ('PENDING','GENERATING','GENERATED','FAILED')),
    storage_key text,
    sha256_hash text CHECK (sha256_hash ~ '^[0-9a-f]{64}$'),
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    lease_until timestamptz,
    generated_at timestamptz,
    safe_error_code text,
    FOREIGN KEY (merchant_id,invoice_id) REFERENCES invoices(merchant_id,id),
    UNIQUE (merchant_id,invoice_id,template_version),
    CHECK (generation_status <> 'GENERATED' OR (storage_key IS NOT NULL AND sha256_hash IS NOT NULL AND generated_at IS NOT NULL))
);

CREATE TABLE idempotency_keys (
    merchant_id uuid NOT NULL REFERENCES merchants(id),
    operation text NOT NULL,
    request_id text NOT NULL CHECK (btrim(request_id) <> ''),
    request_hash text NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    response_data jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (merchant_id,operation,request_id)
);

-- Aggregate views avoid duplicated mutable stock/credit caches. For stock policy
-- and credit-limit checks the posting adapter must lock the corresponding product
-- and party rows FOR UPDATE, in sorted ID order, before checking these balances.
CREATE VIEW inventory_balances WITH (security_invoker=true) AS
SELECT p.merchant_id,p.id AS product_id,coalesce(sum(m.quantity_delta),0)::numeric(18,3) AS quantity_on_hand
FROM products p LEFT JOIN inventory_movements m ON m.merchant_id=p.merchant_id AND m.product_id=p.id
GROUP BY p.merchant_id,p.id;

CREATE VIEW party_balances WITH (security_invoker=true) AS
SELECT p.merchant_id,p.id AS party_id,
    coalesce(sum(m.amount_delta) FILTER (WHERE m.balance_type='RECEIVABLE'),0)::numeric(18,2) AS udhaar_balance,
    coalesce(sum(m.amount_delta) FILTER (WHERE m.balance_type='PAYABLE'),0)::numeric(18,2) AS payable_balance
FROM parties p LEFT JOIN party_balance_movements m ON m.merchant_id=p.merchant_id AND m.party_id=p.id
GROUP BY p.merchant_id,p.id;

CREATE INDEX messages_received_idx ON workflow_messages(merchant_id,received_at DESC);
CREATE INDEX candidates_pending_idx ON transaction_candidates(merchant_id,posting_status,expires_at);
CREATE INDEX transactions_business_date_idx ON transactions(merchant_id,business_date DESC);
CREATE INDEX payments_transaction_idx ON payments(merchant_id,transaction_id);
CREATE INDEX inventory_product_idx ON inventory_movements(merchant_id,product_id);
CREATE INDEX balances_party_idx ON party_balance_movements(merchant_id,party_id);
CREATE INDEX items_transaction_idx ON transaction_items(merchant_id,transaction_id);
CREATE INDEX journal_lines_entry_idx ON journal_lines(merchant_id,journal_entry_id);
CREATE INDEX products_aliases_idx ON products USING gin(aliases);
CREATE INDEX parties_aliases_idx ON parties USING gin(aliases);

CREATE FUNCTION reject_financial_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Committed records are immutable; create an explicit reversal';
END $$;

CREATE FUNCTION guard_candidate() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Candidate evidence cannot be deleted'; END IF;
    IF TG_OP='INSERT' THEN
        NEW.original_extraction := NEW.extracted_snapshot;
        NEW.originally_needs_review := NEW.posting_status='NEEDS_REVIEW'
            OR NEW.extracted_snapshot->>'validation_status' <> 'ACCEPTED';
        NEW.review_resolved := false;
        IF NEW.posting_status NOT IN ('NEEDS_REVIEW','PENDING_CONFIRMATION','REJECTED') THEN
            RAISE EXCEPTION 'A candidate cannot be inserted in a terminal state';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.posting_status IN ('POSTED','REJECTED','CANCELLED','EXPIRED') THEN
        RAISE EXCEPTION 'Terminal candidates are immutable';
    END IF;
    IF (NEW.merchant_id,NEW.id,NEW.source_message_id,NEW.candidate_index,NEW.original_extraction,
        NEW.originally_needs_review,NEW.created_at,NEW.expires_at) IS DISTINCT FROM
       (OLD.merchant_id,OLD.id,OLD.source_message_id,OLD.candidate_index,OLD.original_extraction,
        OLD.originally_needs_review,OLD.created_at,OLD.expires_at) THEN
        RAISE EXCEPTION 'Candidate ownership, source, original extraction and expiry are immutable';
    END IF;
    IF NEW.extracted_snapshot IS DISTINCT FROM OLD.extracted_snapshot THEN
        IF NEW.revision <> OLD.revision+1 OR NEW.reviewed_by_phone IS NULL
           OR coalesce(btrim(NEW.latest_review_note),'')='' OR OLD.expires_at <= statement_timestamp() THEN
            RAISE EXCEPTION 'Review requires next revision, authorized actor, note and unexpired candidate';
        END IF;
        INSERT INTO candidate_reviews(merchant_id,candidate_id,from_revision,to_revision,
            previous_snapshot,corrected_snapshot,reviewed_by_phone,review_note)
        VALUES(OLD.merchant_id,OLD.id,OLD.revision,NEW.revision,OLD.extracted_snapshot,
            NEW.extracted_snapshot,NEW.reviewed_by_phone,NEW.latest_review_note);
        NEW.review_resolved := NEW.extracted_snapshot->>'validation_status'='ACCEPTED'
            AND NOT coalesce((NEW.extracted_snapshot->>'needs_clarification')::boolean,true)
            AND NOT coalesce((NEW.extracted_snapshot->>'requires_review')::boolean,true)
            AND NEW.extracted_snapshot->'missing_fields'='[]'::jsonb;
    ELSIF NEW.revision <> OLD.revision OR NEW.review_resolved IS DISTINCT FROM OLD.review_resolved THEN
        RAISE EXCEPTION 'Review resolution requires an audited snapshot revision';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER candidate_guard BEFORE INSERT OR UPDATE OR DELETE ON transaction_candidates
FOR EACH ROW EXECUTE FUNCTION guard_candidate();

CREATE FUNCTION guard_confirmation() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE c transaction_candidates; sender text;
BEGIN
    SELECT * INTO STRICT c FROM transaction_candidates
        WHERE merchant_id=NEW.merchant_id AND id=NEW.candidate_id FOR UPDATE;
    IF NOT EXISTS(SELECT 1 FROM merchants WHERE id=NEW.merchant_id AND phone_e164=NEW.actor_phone AND is_approved) THEN
        RAISE EXCEPTION 'Phone is not approved for this merchant';
    END IF;
    SELECT sender_phone INTO STRICT sender FROM workflow_messages
        WHERE merchant_id=NEW.merchant_id AND id=NEW.source_message_id;
    IF sender <> NEW.actor_phone OR c.revision <> NEW.candidate_revision
       OR c.snapshot_sha256 <> NEW.expected_snapshot_sha256
       OR c.expires_at <= statement_timestamp()
       OR c.posting_status NOT IN ('NEEDS_REVIEW','PENDING_CONFIRMATION') THEN
        RAISE EXCEPTION 'Unauthorized, stale, expired or terminal confirmation';
    END IF;
    IF NEW.action='CONFIRM' AND (c.posting_status <> 'PENDING_CONFIRMATION'
       OR c.validation_status <> 'ACCEPTED'
       OR c.extracted_snapshot->>'transaction_type' IN ('query','unknown')
       OR coalesce((c.extracted_snapshot->>'needs_clarification')::boolean,true)
       OR coalesce((c.extracted_snapshot->>'requires_review')::boolean,true)
       OR c.extracted_snapshot->'missing_fields' <> '[]'::jsonb
       OR (c.originally_needs_review AND NOT c.review_resolved)) THEN
        RAISE EXCEPTION 'Candidate still requires deterministic validation or review';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER confirmation_guard BEFORE INSERT ON confirmations
FOR EACH ROW EXECUTE FUNCTION guard_confirmation();

-- Final decision, candidate state and committed header must succeed atomically.
CREATE FUNCTION check_candidate_decision() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE candidate uuid; owner uuid; c transaction_candidates; d confirmations; n integer;
BEGIN
    owner := NEW.merchant_id;
    IF TG_TABLE_NAME='transaction_candidates' THEN candidate := NEW.id;
    ELSE candidate := NEW.candidate_id; END IF;
    SELECT * INTO STRICT c FROM transaction_candidates WHERE merchant_id=owner AND id=candidate;
    SELECT * INTO d FROM confirmations WHERE merchant_id=owner AND candidate_id=candidate;
    SELECT count(*) INTO n FROM transactions WHERE merchant_id=owner AND candidate_id=candidate;
    IF d.id IS NULL THEN
        IF c.posting_status IN ('POSTED','CANCELLED') OR n<>0 THEN
            RAISE EXCEPTION 'Terminal decision requires explicit candidate confirmation';
        END IF;
    ELSIF d.action='CONFIRM' THEN
        IF c.posting_status <> 'POSTED' OR n<>1 THEN
            RAISE EXCEPTION 'Confirmation must atomically post exactly one transaction';
        END IF;
    ELSIF c.posting_status <> 'CANCELLED' OR n<>0 THEN
        RAISE EXCEPTION 'Cancellation must not post a transaction';
    END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER candidate_decision_check AFTER INSERT OR UPDATE ON transaction_candidates
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_candidate_decision();
CREATE CONSTRAINT TRIGGER confirmation_decision_check AFTER INSERT ON confirmations
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_candidate_decision();

CREATE FUNCTION guard_transaction_insert() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE d confirmations; c transaction_candidates; expected_intent text;
BEGIN
    SELECT * INTO STRICT d FROM confirmations WHERE merchant_id=NEW.merchant_id AND id=NEW.confirmation_id;
    IF d.action <> 'CONFIRM' OR d.candidate_id <> NEW.candidate_id THEN
        RAISE EXCEPTION 'Transaction requires its own explicit confirmation';
    END IF;
    SELECT * INTO STRICT c FROM transaction_candidates WHERE merchant_id=NEW.merchant_id AND id=NEW.candidate_id;
    expected_intent := CASE c.extracted_snapshot->>'transaction_type'
        WHEN 'sale' THEN 'SALE_INVOICE' WHEN 'purchase' THEN 'PURCHASE_ENTRY'
        WHEN 'payment_received' THEN 'UDHAAR_COLLECTION' WHEN 'payment_made' THEN 'EXPENSE'
        WHEN 'stock_added' THEN 'PRODUCTION_LOG' WHEN 'stock_adjustment' THEN 'PRODUCTION_LOG'
        WHEN 'opening_balance' THEN 'OPENING_BALANCE' WHEN 'reversal' THEN 'REVERSAL' END;
    IF expected_intent IS DISTINCT FROM NEW.intent THEN
        RAISE EXCEPTION 'Committed intent differs from the approved candidate';
    END IF;
    IF NEW.intent IN ('SALE_INVOICE','PURCHASE_ENTRY','UDHAAR_COLLECTION','EXPENSE') AND
       ((c.extracted_snapshot->>'total_amount')::numeric IS DISTINCT FROM NEW.subtotal+NEW.tax_amount-NEW.discount_amount
        OR (c.extracted_snapshot->>'amount_paid')::numeric IS DISTINCT FROM NEW.amount_paid
        OR (c.extracted_snapshot->>'credit_amount')::numeric IS DISTINCT FROM NEW.credit_amount) THEN
        RAISE EXCEPTION 'Committed amounts differ from the approved candidate';
    END IF;
    NEW.created_in_xid := pg_current_xact_id();
    RETURN NEW;
END $$;
CREATE TRIGGER transaction_insert_guard BEFORE INSERT ON transactions
FOR EACH ROW EXECUTE FUNCTION guard_transaction_insert();

-- Components may only be inserted alongside their parent, never appended later.
CREATE FUNCTION guard_component_insert() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE tid uuid; parent_xid xid8;
BEGIN
    IF TG_TABLE_NAME='journal_lines' THEN
        SELECT transaction_id INTO STRICT tid FROM journal_entries
            WHERE merchant_id=NEW.merchant_id AND id=NEW.journal_entry_id;
    ELSE
        tid := NEW.transaction_id;
    END IF;
    SELECT created_in_xid INTO STRICT parent_xid FROM transactions WHERE merchant_id=NEW.merchant_id AND id=tid;
    IF parent_xid <> pg_current_xact_id() THEN
        RAISE EXCEPTION 'Components must be inserted in the original posting transaction';
    END IF;
    RETURN NEW;
END $$;

-- Deferred until all lines exist. No empty or unbalanced journal can commit.
CREATE FUNCTION check_posting_totals() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE paid numeric; items numeric; debits numeric; credits numeric; entries integer;
BEGIN
    SELECT coalesce(sum(amount),0) INTO paid FROM payments WHERE merchant_id=NEW.merchant_id AND transaction_id=NEW.id;
    SELECT coalesce(sum(line_total),0) INTO items FROM transaction_items WHERE merchant_id=NEW.merchant_id AND transaction_id=NEW.id;
    IF paid <> NEW.amount_paid OR EXISTS(SELECT 1 FROM payments WHERE merchant_id=NEW.merchant_id
        AND transaction_id=NEW.id AND currency <> NEW.currency) THEN
        RAISE EXCEPTION 'Payment legs must equal committed amount paid and currency';
    END IF;
    IF NEW.intent IN ('SALE_INVOICE','PURCHASE_ENTRY') AND items <> NEW.subtotal THEN
        RAISE EXCEPTION 'Item totals must equal committed subtotal';
    END IF;
    SELECT count(*) INTO entries FROM journal_entries WHERE merchant_id=NEW.merchant_id AND transaction_id=NEW.id;
    IF NEW.total_amount > 0 OR NEW.amount_paid > 0 OR entries > 0 THEN
        SELECT coalesce(sum(l.debit),0),coalesce(sum(l.credit),0) INTO debits,credits
        FROM journal_entries e JOIN journal_lines l ON l.merchant_id=e.merchant_id AND l.journal_entry_id=e.id
        WHERE e.merchant_id=NEW.merchant_id AND e.transaction_id=NEW.id;
        IF entries<>1 OR debits=0 OR debits<>credits THEN
            RAISE EXCEPTION 'Committed financial transaction needs one balanced nonempty journal';
        END IF;
    END IF;
    IF NEW.intent='UDHAAR_COLLECTION' AND EXISTS(SELECT 1 FROM inventory_movements
        WHERE merchant_id=NEW.merchant_id AND transaction_id=NEW.id) THEN
        RAISE EXCEPTION 'Repayment cannot move inventory';
    END IF;
    IF NEW.intent IN ('SALE_INVOICE','UDHAAR_COLLECTION') THEN
        IF coalesce((SELECT sum(amount_delta) FROM party_balance_movements WHERE merchant_id=NEW.merchant_id
            AND transaction_id=NEW.id AND balance_type='RECEIVABLE' AND party_id=NEW.party_id),0)
           <> (CASE WHEN NEW.intent='SALE_INVOICE' THEN NEW.credit_amount ELSE -NEW.amount_paid END) THEN
            RAISE EXCEPTION 'Receivable movement must equal credit created or repayment collected';
        END IF;
    END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER posting_totals_check AFTER INSERT ON transactions
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_posting_totals();

CREATE FUNCTION guard_invoice() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE t transactions;
BEGIN
    SELECT * INTO STRICT t FROM transactions WHERE merchant_id=NEW.merchant_id AND id=NEW.transaction_id;
    IF t.intent <> 'SALE_INVOICE' OR
       (NEW.currency,NEW.subtotal,NEW.tax_amount,NEW.discount_amount,NEW.total_amount,NEW.amount_paid,NEW.amount_due)
       IS DISTINCT FROM (t.currency,t.subtotal,t.tax_amount,t.discount_amount,t.total_amount,t.amount_paid,t.credit_amount) THEN
        RAISE EXCEPTION 'Invoice must match its committed sale';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER invoice_guard BEFORE INSERT ON invoices FOR EACH ROW EXECUTE FUNCTION guard_invoice();

DO $$
DECLARE tab text;
BEGIN
    FOREACH tab IN ARRAY ARRAY['transactions','transaction_items','payments','party_balance_movements',
        'inventory_movements','journal_entries','journal_lines','confirmations','candidate_reviews','invoices'] LOOP
        EXECUTE format('CREATE TRIGGER immutable_rows BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION reject_financial_mutation()',tab);
    END LOOP;
    FOREACH tab IN ARRAY ARRAY['transaction_items','payments','party_balance_movements',
        'inventory_movements','journal_entries','journal_lines'] LOOP
        EXECUTE format('CREATE TRIGGER component_insert_guard BEFORE INSERT ON %I FOR EACH ROW EXECUTE FUNCTION guard_component_insert()',tab);
    END LOOP;
END $$;

-- Default-deny tenant policies, including for table owners unless SUPERUSER/BYPASSRLS.
-- The app role needs ordinary SELECT/INSERT/UPDATE privileges plus these policies.
DO $$
DECLARE tab text;
BEGIN
    ALTER TABLE merchants ENABLE ROW LEVEL SECURITY;
    ALTER TABLE merchants FORCE ROW LEVEL SECURITY;
    CREATE POLICY merchant_scope ON merchants USING
        (id = nullif(current_setting('app.merchant_id',true),'')::uuid AND is_approved)
        WITH CHECK (id = nullif(current_setting('app.merchant_id',true),'')::uuid AND is_approved);
    FOREACH tab IN ARRAY ARRAY['parties','products','workflow_messages','transaction_candidates','candidate_reviews',
        'conversations','confirmations','transactions','transaction_items','payments','party_balance_movements',
        'inventory_movements','journal_entries','journal_lines','invoices','invoice_artifacts','idempotency_keys'] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',tab);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',tab);
        EXECUTE format('CREATE POLICY merchant_scope ON %I USING (merchant_id = nullif(current_setting(''app.merchant_id'',true),'''')::uuid AND EXISTS (SELECT 1 FROM merchants m WHERE m.id=merchant_id AND m.is_approved)) WITH CHECK (merchant_id = nullif(current_setting(''app.merchant_id'',true),'''')::uuid AND EXISTS (SELECT 1 FROM merchants m WHERE m.id=merchant_id AND m.is_approved))',tab);
    END LOOP;
END $$;

COMMENT ON TABLE transactions IS 'Append-only committed ledger. Insert all components and terminal candidate state in one SQL transaction.';
COMMENT ON TABLE merchants IS 'One explicitly approved WhatsApp user phone per merchant; business transport phone is not a merchant identity.';
COMMENT ON TABLE transaction_candidates IS 'Schema 1.1 snapshots; DB hash is PostgreSQL JSONB serialization, not the legacy Firestore hash.';
COMMENT ON TABLE invoices IS 'BASIC by default. Tax details and an implemented tax-rule version are required before declaring a tax invoice.';
COMMIT;
