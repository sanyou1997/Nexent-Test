-- Bind to an existing tenant selected by the trusted local administrator.
-- The caller supplies tenant, actor, models and parser via SQLAlchemy bound parameters.
SELECT pg_advisory_xact_lock(hashtext(:tenant), hashtext('nexent-test-model-seed'));

INSERT INTO nexent.model_record_t
  (tenant_id, created_by, updated_by, model_name, display_name, model_type, model_factory,
   max_tokens, timeout_seconds, temperature, chunk_batch, delete_flag)
SELECT :tenant, :actor, :actor, m.model_name, m.display_name, m.model_type, m.model_factory,
       m.max_tokens, m.timeout_seconds, m.temperature, m.chunk_batch, 'N'
FROM jsonb_to_recordset(CAST(:models AS jsonb)) AS m(
  model_name text, display_name text, model_type text, model_factory text,
  max_tokens integer, timeout_seconds integer, temperature double precision, chunk_batch integer)
WHERE NOT EXISTS (
  SELECT 1 FROM nexent.model_record_t e
  WHERE e.tenant_id = :tenant AND e.display_name = m.display_name AND e.delete_flag = 'N');

INSERT INTO nexent.document_parser_settings(tenant_id, agent_id, settings, updated_by)
VALUES (:tenant, 0, CAST(:parser AS jsonb), :actor)
ON CONFLICT (tenant_id, agent_id) DO NOTHING;
