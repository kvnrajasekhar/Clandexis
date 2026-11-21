-- Add durable WhatsApp onboarding state to an existing PostgreSQL database.
-- Apply once with a migration-owner connection before deploying code that uses it.
BEGIN;

CREATE TABLE IF NOT EXISTS merchant_onboarding_sessions (
    sender_phone text PRIMARY KEY CHECK (sender_phone ~ '^\+[1-9][0-9]{7,14}$'),
    session_state jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(session_state) = 'object'),
    step text NOT NULL DEFAULT 'INIT'
        CHECK (step IN ('INIT','AWAITING_LANGUAGE','AWAITING_PIN','AWAITING_MSME_DETAILS',
            'AWAITING_CONFIRMATION','COMPLETED')),
    revision bigint NOT NULL DEFAULT 1 CHECK (revision > 0),
    expires_at timestamptz NOT NULL DEFAULT (now() + interval '30 days'),
    lease_token uuid,
    lease_until timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((lease_token IS NULL) = (lease_until IS NULL))
);

CREATE TABLE IF NOT EXISTS merchant_onboarding_events (
    sender_phone text NOT NULL
        REFERENCES merchant_onboarding_sessions(sender_phone) ON DELETE CASCADE,
    external_message_id text NOT NULL CHECK (btrim(external_message_id) <> ''),
    message_type text NOT NULL,
    interactive_id text,
    processing_status text NOT NULL DEFAULT 'RECEIVED'
        CHECK (processing_status IN ('RECEIVED','PROCESSING','DEFERRED','DONE','FAILED')),
    received_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL DEFAULT (now() + interval '30 days'),
    processed_at timestamptz,
    PRIMARY KEY (sender_phone, external_message_id)
);

CREATE INDEX IF NOT EXISTS onboarding_events_expiry_idx
    ON merchant_onboarding_events(sender_phone, expires_at);

ALTER TABLE merchant_onboarding_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE merchant_onboarding_sessions FORCE ROW LEVEL SECURITY;
ALTER TABLE merchant_onboarding_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE merchant_onboarding_events FORCE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = current_schema()
          AND tablename = 'merchants'
          AND policyname = 'merchant_phone_lookup'
    ) THEN
        CREATE POLICY merchant_phone_lookup ON merchants FOR SELECT USING
            (is_approved AND phone_e164 = nullif(current_setting('app.phone_lookup',true),''));
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = current_schema()
          AND tablename = 'merchant_onboarding_sessions'
          AND policyname = 'onboarding_phone_scope'
    ) THEN
        CREATE POLICY onboarding_phone_scope ON merchant_onboarding_sessions USING
            (sender_phone = nullif(current_setting('app.onboarding_phone',true),''))
            WITH CHECK (sender_phone = nullif(current_setting('app.onboarding_phone',true),''));
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = current_schema()
          AND tablename = 'merchant_onboarding_events'
          AND policyname = 'onboarding_phone_scope'
    ) THEN
        CREATE POLICY onboarding_phone_scope ON merchant_onboarding_events USING
            (sender_phone = nullif(current_setting('app.onboarding_phone',true),''))
            WITH CHECK (sender_phone = nullif(current_setting('app.onboarding_phone',true),''));
    END IF;
END $$;

COMMENT ON TABLE merchant_onboarding_sessions IS
    'Active onboarding state; sensitive fields expire after 30 days of inactivity and are cleared on activation.';
COMMENT ON TABLE merchant_onboarding_events IS
    'Short-lived idempotency records; stores message identifiers and types, never raw inbound content.';

-- Grant only the privileges required by the configured runtime role, for example:
-- GRANT SELECT, INSERT, UPDATE, DELETE ON merchant_onboarding_sessions, merchant_onboarding_events TO clandexis_app;

COMMIT;
