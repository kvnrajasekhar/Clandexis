-- Run ONLY in a fresh disposable database after models/postgres_schema.sql.
-- psql -v ON_ERROR_STOP=1 -f tests/sql/postgres_schema_checks.sql
\set ON_ERROR_STOP on
CREATE FUNCTION pg_temp.assert_ok(value boolean,label text) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    IF value IS DISTINCT FROM true THEN RAISE EXCEPTION 'FAIL %',label; END IF;
    RAISE NOTICE 'PASS %',label;
END $$;
CREATE FUNCTION pg_temp.expect_error(statement text,expected text,label text) RETURNS void LANGUAGE plpgsql AS $$
DECLARE failed boolean := false;
BEGIN
    BEGIN
        EXECUTE statement;
        SET CONSTRAINTS ALL IMMEDIATE;
    EXCEPTION WHEN OTHERS THEN
        IF position(expected IN SQLERRM)=0 THEN RAISE; END IF;
        failed := true;
    END;
    PERFORM pg_temp.assert_ok(failed,label);
END $$;

INSERT INTO merchants(id,phone_e164,business_name,is_approved) VALUES
('11111111-1111-1111-1111-111111111111','+918001111111','Merchant A',true),
('22222222-2222-2222-2222-222222222222','+919002222222','Merchant B',true);
INSERT INTO parties(id,merchant_id,display_name) VALUES
('33333333-3333-3333-3333-333333333333','11111111-1111-1111-1111-111111111111','Raju');
INSERT INTO products(id,merchant_id,sku_code,product_name,unit) VALUES
('44444444-4444-4444-4444-444444444444','11111111-1111-1111-1111-111111111111','SHIRT','Cotton Shirt','pieces'),
('55555555-5555-5555-5555-555555555555','22222222-2222-2222-2222-222222222222','SHIRT','Cotton Shirt','pieces');

CREATE FUNCTION pg_temp.stage(snapshot jsonb,posting text DEFAULT 'PENDING_CONFIRMATION') RETURNS uuid LANGUAGE plpgsql AS $$
DECLARE mid uuid := '11111111-1111-1111-1111-111111111111'; msg uuid; candidate uuid;
BEGIN
    INSERT INTO workflow_messages(merchant_id,sender_phone,source_channel,external_message_id,message_type)
    VALUES(mid,'+918001111111','WHATSAPP',gen_random_uuid()::text,'text') RETURNING id INTO msg;
    INSERT INTO transaction_candidates(merchant_id,source_message_id,original_extraction,extracted_snapshot,posting_status,expires_at)
    VALUES(mid,msg,snapshot,snapshot,posting,now()+interval '30 minutes') RETURNING id INTO candidate;
    RETURN candidate;
END $$;
CREATE FUNCTION pg_temp.post(kind text,quantity numeric,total numeric,paid numeric,credit numeric) RETURNS uuid LANGUAGE plpgsql AS $$
DECLARE mid uuid := '11111111-1111-1111-1111-111111111111'; party uuid := '33333333-3333-3333-3333-333333333333';
    product uuid := '44444444-4444-4444-4444-444444444444'; cid uuid; msg uuid; decision uuid; tid uuid; jid uuid; typ text;
BEGIN
    typ := CASE kind WHEN 'SALE_INVOICE' THEN 'sale' WHEN 'UDHAAR_COLLECTION' THEN 'payment_received' ELSE 'opening_balance' END;
    cid := pg_temp.stage(jsonb_build_object('schema_version','1.1','transaction_type',typ,'validation_status','ACCEPTED',
        'needs_clarification',false,'requires_review',false,'missing_fields','[]'::jsonb,
        'total_amount',total,'amount_paid',paid,'credit_amount',credit));
    INSERT INTO workflow_messages(merchant_id,sender_phone,source_channel,external_message_id,message_type)
    VALUES(mid,'+918001111111','WHATSAPP',gen_random_uuid()::text,'interactive_button') RETURNING id INTO msg;
    INSERT INTO confirmations(merchant_id,candidate_id,source_message_id,request_id,action,actor_phone,candidate_revision,expected_snapshot_sha256)
    SELECT mid,cid,msg,msg::text,'CONFIRM','+918001111111',revision,snapshot_sha256 FROM transaction_candidates WHERE id=cid
    RETURNING id INTO decision;
    INSERT INTO transactions(merchant_id,candidate_id,confirmation_id,intent,party_id,business_date,subtotal,amount_paid,credit_amount,payment_status)
    VALUES(mid,cid,decision,kind,party,current_date,total,paid,credit,
        CASE WHEN kind='OPENING_BALANCE' THEN 'NOT_APPLICABLE' WHEN credit=0 THEN 'PAID' ELSE 'PARTIAL' END) RETURNING id INTO tid;
    IF kind='SALE_INVOICE' THEN
        INSERT INTO transaction_items(merchant_id,transaction_id,product_id,item_description,quantity,unit,unit_price)
        VALUES(mid,tid,product,'Cotton Shirt',quantity,'pieces',200);
        INSERT INTO party_balance_movements(merchant_id,transaction_id,party_id,balance_type,amount_delta)
        VALUES(mid,tid,party,'RECEIVABLE',credit);
    ELSIF kind='UDHAAR_COLLECTION' THEN
        INSERT INTO party_balance_movements(merchant_id,transaction_id,party_id,balance_type,amount_delta)
        VALUES(mid,tid,party,'RECEIVABLE',-paid);
    END IF;
    IF kind IN ('SALE_INVOICE','OPENING_BALANCE') THEN
        INSERT INTO inventory_movements(merchant_id,transaction_id,product_id,movement_type,quantity_delta)
        VALUES(mid,tid,product,CASE WHEN kind='SALE_INVOICE' THEN 'SALE_DEDUCT' ELSE 'OPENING_STOCK' END,
            CASE WHEN kind='SALE_INVOICE' THEN -quantity ELSE quantity END);
    END IF;
    IF paid>0 THEN
        INSERT INTO payments(merchant_id,transaction_id,party_id,direction,method,amount,currency)
        VALUES(mid,tid,party,'RECEIVED','UPI',paid,'INR');
        INSERT INTO journal_entries(merchant_id,transaction_id,description) VALUES(mid,tid,kind) RETURNING id INTO jid;
        INSERT INTO journal_lines(merchant_id,journal_entry_id,account_code,account_name,debit,credit)
        VALUES(mid,jid,'UPI','UPI',paid,0);
        IF kind='SALE_INVOICE' THEN
            INSERT INTO journal_lines(merchant_id,journal_entry_id,account_code,account_name,debit,credit)
            VALUES(mid,jid,'AR','Receivable',credit,0),(mid,jid,'SALES','Sales',0,total);
        ELSE
            INSERT INTO journal_lines(merchant_id,journal_entry_id,account_code,account_name,debit,credit)
            VALUES(mid,jid,'AR','Receivable',0,paid);
        END IF;
    END IF;
    UPDATE transaction_candidates SET posting_status='POSTED' WHERE id=cid;
    RETURN tid;
END $$;

SELECT pg_temp.post('OPENING_BALANCE',200,0,0,0);
SELECT pg_temp.post('SALE_INVOICE',10,2000,500,1500) AS sale_id \gset
SELECT pg_temp.assert_ok((SELECT quantity_on_hand=190 FROM inventory_balances WHERE merchant_id='11111111-1111-1111-1111-111111111111'),'sale stock deduction');
SELECT pg_temp.assert_ok((SELECT udhaar_balance=1500 FROM party_balances WHERE party_id='33333333-3333-3333-3333-333333333333'),'sale receivable');
SELECT pg_temp.post('UDHAAR_COLLECTION',0,300,300,0);
SELECT pg_temp.assert_ok((SELECT quantity_on_hand=190 FROM inventory_balances WHERE merchant_id='11111111-1111-1111-1111-111111111111'),'repayment leaves stock unchanged');
SELECT pg_temp.assert_ok((SELECT udhaar_balance=1200 FROM party_balances WHERE party_id='33333333-3333-3333-3333-333333333333'),'repayment reduces receivable');
SELECT pg_temp.assert_ok((SELECT sum(total_amount)=2000 FROM transactions WHERE intent='SALE_INVOICE'),'repayment leaves sale revenue unchanged');

SELECT pg_temp.expect_error(format('UPDATE transactions SET amount_paid=0 WHERE id=%L',:'sale_id'),'immutable','posted header immutable');
SELECT pg_temp.expect_error('UPDATE journal_lines SET debit=100 WHERE debit>0','immutable','posted journal lines immutable');
SELECT pg_temp.expect_error('DELETE FROM journal_entries','immutable','posted journal deletion blocked');
SELECT pg_temp.expect_error(format('INSERT INTO payments(merchant_id,transaction_id,direction,method,amount,currency) VALUES(%L,%L,''RECEIVED'',''UPI'',1,''INR'')',
    '11111111-1111-1111-1111-111111111111',:'sale_id'),'original posting transaction','late payment append blocked');
SELECT pg_temp.expect_error('SELECT pg_temp.post(''SALE_INVOICE'',10,2100,500,1600)','Item totals','incorrect line totals rejected atomically');

INSERT INTO invoices(merchant_id,transaction_id,invoice_number,invoice_date,currency,merchant_snapshot,buyer_snapshot,items_snapshot,
    subtotal,tax_amount,discount_amount,total_amount,amount_paid,amount_due)
VALUES('11111111-1111-1111-1111-111111111111',:'sale_id','INV-1',current_date,'INR','{}','{}','[]',2000,0,0,2000,500,1500);
SELECT pg_temp.assert_ok((SELECT count(*)=1 FROM invoices),'invoice from committed sale');
SELECT pg_temp.expect_error(format('INSERT INTO invoices(merchant_id,transaction_id,invoice_number,invoice_date,currency,merchant_snapshot,buyer_snapshot,items_snapshot,subtotal,tax_amount,discount_amount,total_amount,amount_paid,amount_due) VALUES(%L,%L,''BAD'',current_date,''INR'',''{}'',''{}'',''[]'',2000,0,0,2000,0,2000)',
    '11111111-1111-1111-1111-111111111111',:'sale_id'),'Invoice must match','invoice amounts checked against commit');

SELECT pg_temp.stage('{"schema_version":"1.1","transaction_type":"sale","validation_status":"NEEDS_REVIEW","needs_clarification":true,"requires_review":true,"missing_fields":["unit_price","payment_details"]}', 'NEEDS_REVIEW') AS review_id \gset
UPDATE transaction_candidates SET extracted_snapshot='{"schema_version":"1.1","transaction_type":"sale","validation_status":"NEEDS_REVIEW","needs_clarification":true,"requires_review":true,"missing_fields":["payment_details"],"unit_price":200}',
    revision=2,reviewed_by_phone='+918001111111',latest_review_note='200 each' WHERE id=:'review_id';
SELECT pg_temp.assert_ok((SELECT count(*)=1 FROM candidate_reviews WHERE candidate_id=:'review_id'),'partial clarification audited');
SELECT pg_temp.assert_ok((SELECT original_extraction->'missing_fields' ? 'unit_price' FROM transaction_candidates WHERE id=:'review_id'),'original extraction preserved');
INSERT INTO conversations(merchant_id,sender_phone,active_candidate_id,candidate_revision,latest_question,missing_fields,detected_language,script_preference,expires_at)
SELECT merchant_id,'+918001111111',id,revision,'Payment entha?','["payment_details"]','te','Latin',expires_at FROM transaction_candidates WHERE id=:'review_id';
SELECT pg_temp.assert_ok((SELECT candidate_revision=2 AND detected_language='te' FROM conversations),'conversation persisted');

-- Independent second merchant cannot reference the first merchant's party.
SELECT pg_temp.expect_error('INSERT INTO transaction_candidates(merchant_id,source_message_id,original_extraction,extracted_snapshot,expires_at) SELECT ''22222222-2222-2222-2222-222222222222'',source_message_id,original_extraction,extracted_snapshot,expires_at FROM transaction_candidates LIMIT 1',
    'foreign key','cross-merchant source FK blocked');

DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='clandexis_schema_test_app') THEN CREATE ROLE clandexis_schema_test_app NOLOGIN; END IF; END $$;
GRANT USAGE ON SCHEMA public TO clandexis_schema_test_app;
GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO clandexis_schema_test_app;
SET ROLE clandexis_schema_test_app;
SELECT set_config('app.merchant_id','22222222-2222-2222-2222-222222222222',false);
SELECT pg_temp.assert_ok((SELECT count(*)=0 FROM transactions),'phone B cannot read phone A transactions');
SELECT pg_temp.assert_ok((SELECT count(*)=0 FROM conversations),'phone B cannot read phone A conversation');
SELECT pg_temp.expect_error('INSERT INTO parties(merchant_id,display_name) VALUES(''11111111-1111-1111-1111-111111111111'',''Foreign'')',
    'row-level security','phone B cannot write phone A records');
SELECT set_config('app.merchant_id','',false);
SELECT pg_temp.assert_ok((SELECT count(*)=0 FROM products),'missing tenant fails closed');
RESET ROLE;

SELECT pg_temp.expect_error(format('INSERT INTO confirmations(merchant_id,candidate_id,source_message_id,request_id,action,actor_phone,candidate_revision,expected_snapshot_sha256) SELECT merchant_id,id,source_message_id,''premature'',''CONFIRM'',''+918001111111'',revision,snapshot_sha256 FROM transaction_candidates WHERE id=%L',:'review_id'),
    'still requires','review candidate cannot confirm');
SELECT pg_temp.expect_error($test$DO $$
DECLARE t uuid;
BEGIN
    t := pg_temp.post('SALE_INVOICE',10,2000,500,1500);
    INSERT INTO journal_lines(merchant_id,journal_entry_id,account_code,account_name,debit,credit)
    SELECT merchant_id,id,'BAD','Extra debit',1,0 FROM journal_entries WHERE transaction_id=t;
END $$ $test$,'balanced','unbalanced journal rejected at commit');
SELECT pg_temp.assert_ok((SELECT count(*)=1 FROM transactions WHERE intent='SALE_INVOICE'),'failed posting rolled back atomically');
SELECT pg_temp.expect_error(format('INSERT INTO invoices(merchant_id,transaction_id,invoice_number,invoice_date,currency,merchant_snapshot,buyer_snapshot,items_snapshot,subtotal,tax_amount,discount_amount,total_amount,amount_paid,amount_due) SELECT merchant_id,transaction_id,''INV-DUP'',invoice_date,currency,merchant_snapshot,buyer_snapshot,items_snapshot,subtotal,tax_amount,discount_amount,total_amount,amount_paid,amount_due FROM invoices WHERE transaction_id=%L',:'sale_id'),
    'unique constraint','duplicate sale invoice blocked');
SELECT pg_temp.expect_error(format('INSERT INTO confirmations(merchant_id,candidate_id,source_message_id,request_id,action,actor_phone,candidate_revision,expected_snapshot_sha256) SELECT c.merchant_id,c.id,c.source_message_id,''duplicate'',''CONFIRM'',''+918001111111'',c.revision,c.snapshot_sha256 FROM transaction_candidates c JOIN transactions t ON c.id=t.candidate_id WHERE t.id=%L',:'sale_id'),
    'terminal confirmation','duplicate confirm cannot post twice');

BEGIN;
INSERT INTO confirmations(merchant_id,candidate_id,source_message_id,request_id,action,actor_phone,candidate_revision,expected_snapshot_sha256)
SELECT merchant_id,id,source_message_id,'cancel-review','CANCEL','+918001111111',revision,snapshot_sha256 FROM transaction_candidates WHERE id=:'review_id';
UPDATE transaction_candidates SET posting_status='CANCELLED' WHERE id=:'review_id';
UPDATE conversations SET active_candidate_id=NULL,candidate_revision=NULL,expires_at=NULL,latest_question=NULL,missing_fields='[]';
COMMIT;
SELECT pg_temp.assert_ok((SELECT count(*)=0 FROM transactions WHERE candidate_id=:'review_id'),'cancel produces zero transactions');
