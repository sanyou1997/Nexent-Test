-- CTAS「识别服务 · 跟随 Nexent」所需的 Nexent 只读账号（需由 Nexent 数据库管理员执行一次）
--
-- 授予范围（最小权限）：
--   1. 只读 nexent.document_parser_settings（各租户的「文件解析」识别服务配置）；
--   2. 只能调用 nexent.ctas_tenant_of_key(key)：按 CTAS 已持有的 API Key 查出它所属的租户，
--      读不到任何 Key 明文、用户信息或其它业务表。
--
-- 执行（把 <强密码> 换成随机字符串，与 ctas-system/.env 中 NEXENT_DB_DSN 的密码一致）：
--   PowerShell：Get-Content docker/nexent-readonly-role.sql -Raw | docker exec -i nexent-postgresql psql -U root -d nexent -v pw=<强密码>
--   bash：     docker exec -i nexent-postgresql psql -U root -d nexent -v pw=<强密码> < docker/nexent-readonly-role.sql
-- 然后在 ctas-system/.env 中加入：
--   NEXENT_DB_DSN=postgresql://ctas_reader:<强密码>@nexent-postgresql:5432/nexent
-- 并执行 docker compose up -d 使其生效。
--
-- 撤销：
--   DROP FUNCTION IF EXISTS nexent.ctas_tenant_of_key(text);
--   REVOKE ALL ON nexent.document_parser_settings FROM ctas_reader;
--   REVOKE USAGE ON SCHEMA nexent FROM ctas_reader; REVOKE CONNECT ON DATABASE nexent FROM ctas_reader;
--   DROP ROLE IF EXISTS ctas_reader;

SELECT CASE WHEN EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ctas_reader')
            THEN format('ALTER ROLE ctas_reader LOGIN PASSWORD %L', :'pw')
            ELSE format('CREATE ROLE ctas_reader LOGIN PASSWORD %L', :'pw') END \gexec

GRANT CONNECT ON DATABASE nexent TO ctas_reader;
GRANT USAGE ON SCHEMA nexent TO ctas_reader;
GRANT SELECT ON nexent.document_parser_settings TO ctas_reader;

CREATE OR REPLACE FUNCTION nexent.ctas_tenant_of_key(p_key text) RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = nexent, pg_temp AS $f$
  SELECT ut.tenant_id::text
  FROM nexent.user_token_info_t t
  JOIN nexent.user_tenant_t ut ON ut.user_id = t.user_id AND ut.delete_flag = 'N'
  WHERE t.access_key = p_key AND t.delete_flag = 'N'
  LIMIT 1
$f$;
REVOKE ALL ON FUNCTION nexent.ctas_tenant_of_key(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION nexent.ctas_tenant_of_key(text) TO ctas_reader;
